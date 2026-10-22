"""Découpage automatique d'un livre entier : les 4 étapes."""
import copy
import io
import json
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient
from pypdf import PdfWriter

from app.assemble import apply_links, assemble, run_assemble
from app.cli import main as cli
from app.lesson import rename_refs, run_lesson
from app.main import app
from app.outline import check_outline, run_outline
from app.pipeline import UnsupportedFile
from app.providers.base import AIProvider, ProviderError
from app.providers.fake import FakeProvider
from app.schemas import AssembleRequest, Lesson, LessonPart, Outline

HEADERS = {"X-API-Key": "test-key"}
client = TestClient(app)


def book(pages: int = 20) -> bytes:
    writer = PdfWriter()
    for _ in range(pages):
        writer.add_blank_page(width=100, height=100)
    out = io.BytesIO()
    writer.write(out)
    return out.getvalue()


def plan(*lessons, unit="الأعداد من 1 إلى 5") -> Outline:
    return Outline.model_validate(
        {"units": [{"title": unit, "lessons": [
            {"title": t, "start_page": a, "end_page": b} for t, a, b in lessons
        ]}]}
    )


EXAMPLE = plan(("العدّ إلى 5", 5, 8), ("كتابة الأرقام", 9, 12), ("الجمع حتى 5", 13, 16))


@pytest.fixture
def sample_lesson(sample) -> dict:
    return sample["units"][0]["lessons"][0]


# --- Étape 1 : vérification du plan ---------------------------------------------------
def codes(issues):
    return [i.code for i in issues]


def test_good_plan_has_no_issue():
    assert check_outline(EXAMPLE, 20) == []


def test_lessons_may_share_one_page():
    assert check_outline(plan(("a", 5, 8), ("b", 8, 12)), 20) == []


def test_overlap_is_an_error():
    assert codes(check_outline(plan(("a", 5, 10), ("b", 8, 12)), 20)) == ["PAGE_OVERLAP"]


def test_gap_is_a_warning():
    issues = check_outline(plan(("a", 5, 8), ("b", 11, 12)), 20)
    assert codes(issues) == ["PAGE_GAP"] and issues[0].level == "warning"
    assert "9-10" in issues[0].message


def test_page_after_the_end_and_reversed_pages():
    assert codes(check_outline(plan(("a", 5, 30)), 20)) == ["PAGE_OUT_OF_RANGE"]
    assert codes(check_outline(plan(("a", 9, 5)), 20)) == ["PAGE_ORDER"]


def test_very_long_lesson_is_a_warning():
    assert codes(check_outline(plan(("a", 1, 30)), 40)) == ["LESSON_TOO_LONG"]


def test_unit_without_lessons_is_an_error():
    outline = Outline.model_validate({"units": [{"title": "u", "lessons": []}]})
    assert codes(check_outline(outline, 10)) == ["UNIT_NO_LESSONS"]


def test_run_outline_labels_every_page():
    class Spy(FakeProvider):
        def generate(self, doc, instructions, repair=None):
            if not hasattr(self, "doc"):  # le 1er appel = le repérage du livre
                self.doc, self.instructions = doc, instructions
            return super().generate(doc, instructions, repair)

    spy = Spy()
    result = run_outline(book(12), "livre.pdf", spy)
    assert result.total_pages == 12
    assert len(spy.doc.pages) == 12 and spy.doc.first_page == 1
    assert spy.instructions.startswith("TASK: OUTLINE")
    assert "=== PAGE n ===" in spy.instructions


# --- Étape 2 : une leçon -----------------------------------------------------------------
class Scripted(AIProvider):
    """Renvoie les réponses prévues, dans l'ordre, et garde ce qu'il a reçu."""

    name, model = "scripted", "test"

    def __init__(self, *answers):
        self.answers, self.calls = list(answers), []

    def generate(self, doc, instructions, repair=None):
        self.calls.append(SimpleNamespace(doc=doc, instructions=instructions, repair=repair))
        answer = self.answers.pop(0)
        if isinstance(answer, Exception):
            raise answer
        return answer if isinstance(answer, str) else json.dumps(answer, ensure_ascii=False)


def test_lesson_gets_only_its_pages_with_book_numbers(sample_lesson):
    ai = Scripted(sample_lesson)
    result = run_lesson(book(20), "livre.pdf", EXAMPLE, 1, 3, ai)
    sent = ai.calls[0].doc
    assert sent.first_page == 13 and len(sent.pages) == 4  # pages 13 à 16 du livre
    assert "<- THIS LESSON" in ai.calls[0].instructions
    assert result.lesson.title == "الجمع حتى 5"  # titre imposé par le plan
    assert result.lesson.ref == "u1-l3"
    assert [s.ref for g in result.lesson.goals for s in g.skills] == ["u1-l3-s1", "u1-l3-s2", "u1-l3-s3", "u1-l3-s4"]
    assert [i for i in result.issues if i.level == "error"] == []


def test_lesson_with_rule_error_is_asked_again(sample_lesson):
    """Le cas de la leçon 2 de l'exemple : 2 bonnes réponses dans un exercice."""
    broken = copy.deepcopy(sample_lesson)
    for option in broken["goals"][0]["skills"][0]["chunks"][2]["options"]:
        option["is_correct"], option["error_category"] = True, None
    ai = Scripted(broken, sample_lesson)
    result = run_lesson(book(20), "livre.pdf", EXAMPLE, 1, 2, ai)
    assert len(ai.calls) == 2
    assert "ONE_CORRECT" in ai.calls[1].repair
    assert result.meta.repaired is True
    assert [i for i in result.issues if i.level == "error"] == []


def test_failed_correction_keeps_the_first_version(sample_lesson):
    broken = copy.deepcopy(sample_lesson)
    broken["goals"][0]["skills"][0]["chunks"] = broken["goals"][0]["skills"][0]["chunks"][:2]
    ai = Scripted(broken, "pas du JSON")
    result = run_lesson(book(20), "livre.pdf", EXAMPLE, 1, 1, ai)
    assert "SKILL_MIN_CHUNKS" in codes(result.issues)


def test_unknown_lesson_is_refused(sample_lesson):
    with pytest.raises(UnsupportedFile):
        run_lesson(book(20), "livre.pdf", EXAMPLE, 1, 9, Scripted(sample_lesson))


def test_rename_makes_refs_unique(sample_lesson):
    lesson = Lesson.model_validate(sample_lesson)
    l1, l3 = rename_refs(lesson, 1, 1), rename_refs(lesson, 1, 3)
    refs1 = [s.ref for g in l1.goals for s in g.skills]
    refs3 = [s.ref for g in l3.goals for s in g.skills]
    assert not set(refs1) & set(refs3)
    # les prérequis internes suivent : s2 a besoin de s1 dans la même leçon
    assert l3.goals[0].skills[1].prerequisites == ["u1-l3-s1"]


# --- Étapes 3 et 4 -----------------------------------------------------------------------
def three_lessons(sample_lesson) -> dict:
    lesson = Lesson.model_validate(sample_lesson)
    return {(1, i): rename_refs(lesson, 1, i) for i in (1, 2, 3)}


def test_assemble_orders_lessons_under_their_unit(sample_lesson):
    draft, issues = assemble(EXAMPLE, three_lessons(sample_lesson), 16, "livre.pdf", "ar")
    assert issues == []
    unit = draft.units[0]
    assert unit.ref == "u1" and unit.source_page == 5
    assert [l.ref for l in unit.lessons] == ["u1-l1", "u1-l2", "u1-l3"]


def test_the_example_cycle_is_detected(sample_lesson):
    """Le 4e lien de l'exemple (« compter » aurait besoin de « calculer ») crée une boucle."""
    ai = Scripted({"links": [
        {"skill": "u1-l2-s1", "prerequisites": ["u1-l1-s1"]},
        {"skill": "u1-l3-s1", "prerequisites": ["u1-l1-s2"]},
        {"skill": "u1-l1-s2", "prerequisites": ["u1-l3-s2"]},
    ]})
    parts = [LessonPart(unit_index=u, lesson_index=l, lesson=x) for (u, l), x in three_lessons(sample_lesson).items()]
    request = AssembleRequest(subject=16, source_file="livre.pdf", language="ar", outline=EXAMPLE, lessons=parts)
    result = run_assemble(request, ai)
    assert "TASK: LINKS" in ai.calls[0].instructions
    assert "u1-l3-s3 [operational]" in ai.calls[0].doc.text  # la liste des skills, sans exercices
    found = codes(result.issues)
    assert "PREREQ_LATER" in found  # le lien vers une leçon plus loin est signalé
    assert "PREREQ_CYCLE" in found  # et la boucle est trouvée par rules.py
    skill = result.draft.units[0].lessons[2].goals[0].skills[0]
    assert "u1-l1-s2" in skill.prerequisites


def test_links_to_unknown_skills_are_ignored(sample_lesson):
    draft, _ = assemble(EXAMPLE, three_lessons(sample_lesson), 16, "livre.pdf", "ar")
    draft, issues = apply_links(draft, {"u1-l2-s1": ["u9-l9-s9", "u1-l2-s1"], "inconnue": ["u1-l1-s1"]})
    assert codes(issues) == ["LINK_UNKNOWN", "LINK_UNKNOWN"]
    assert draft.units[0].lessons[1].goals[0].skills[0].prerequisites == []


def test_missing_lesson_is_reported_once(sample_lesson):
    lessons = three_lessons(sample_lesson)
    del lessons[(1, 2)]
    parts = [LessonPart(unit_index=u, lesson_index=l, lesson=x) for (u, l), x in lessons.items()]
    request = AssembleRequest(subject=16, source_file="livre.pdf", outline=EXAMPLE, lessons=parts, find_links=False)
    result = run_assemble(request, None)
    assert codes(result.issues) == ["LESSON_MISSING"]
    assert result.issues[0].path == "units[0].lessons[1]"
    assert result.draft.units[0].lessons[1].goals == []


def test_draft_is_delivered_even_if_links_fail(sample_lesson):
    parts = [LessonPart(unit_index=u, lesson_index=l, lesson=x) for (u, l), x in three_lessons(sample_lesson).items()]
    request = AssembleRequest(subject=16, source_file="livre.pdf", outline=EXAMPLE, lessons=parts)
    result = run_assemble(request, Scripted(ProviderError("quota")))
    assert codes(result.issues) == ["LINKS_FAILED"]
    assert len(result.draft.units[0].lessons) == 3


# --- Fournisseurs : les pages gardent leur numéro du livre --------------------------------
def test_gemini_labels_each_page():
    from app.providers.base import SourceDocument
    from app.providers.gemini import GeminiProvider

    gemini = GeminiProvider("cle-de-test", "modele")
    seen = {}

    def fake_generate_content(model, contents, config):
        seen["parts"] = contents[0].parts
        return SimpleNamespace(text="{}")

    gemini._client = SimpleNamespace(models=SimpleNamespace(generate_content=fake_generate_content))
    doc = SourceDocument("l.pdf", "application/pdf", b"%PDF", pages=[b"%PDF-a", b"%PDF-b"], first_page=13)
    gemini.generate(doc, "consignes")
    texts = [p.text for p in seen["parts"] if p.text]
    assert texts == ["consignes", "=== PAGE 13 ===", "=== PAGE 14 ==="]


def test_mistral_ocr_uses_book_page_numbers():
    from app.providers.base import SourceDocument
    from app.providers.mistral import MistralProvider

    mistral = MistralProvider("cle-de-test", "m", "ocr")
    pages = [SimpleNamespace(markdown="# فوق، تحت\n" + "x" * 50), SimpleNamespace(markdown="page 2")]
    mistral._client = SimpleNamespace(ocr=SimpleNamespace(process=lambda **kw: SimpleNamespace(pages=pages)))
    text = mistral._ocr(SourceDocument("l.pdf", "application/pdf", b"%PDF", first_page=13, excerpt_chars=10))
    assert text.startswith("=== PAGE 13 ===\n# فوق، تحت")
    assert "=== PAGE 14 ===" in text and "x" * 20 not in text


# --- API : les 3 adresses, dans l'ordre où FATIN les appelle ------------------------------
def test_api_full_book_flow():
    pdf = book(12)
    files = lambda: {"file": ("livre.pdf", pdf, "application/pdf")}

    r = client.post("/api/outline/", headers=HEADERS, files=files(), data={"language": "ar"})
    assert r.status_code == 200, r.text
    outline = r.json()["outline"]
    assert r.json()["total_pages"] == 12 and r.json()["issues"] == []

    lessons = []
    for li in range(1, len(outline["units"][0]["lessons"]) + 1):
        r = client.post(
            "/api/parse-lesson/", headers=HEADERS, files=files(),
            data={"outline": json.dumps(outline, ensure_ascii=False), "unit_index": "1", "lesson_index": str(li)},
        )
        assert r.status_code == 200, r.text
        lessons.append(r.json())  # FATIN peut renvoyer la réponse telle quelle

    r = client.post("/api/assemble/", headers=HEADERS, json={
        "subject": 16, "source_file": "livre.pdf", "language": "ar", "outline": outline, "lessons": lessons,
    })
    assert r.status_code == 200, r.text
    body = r.json()
    assert len(body["draft"]["units"][0]["lessons"]) == 3
    assert [i for i in body["issues"] if i["level"] == "error"] == []
    assert body["draft"]["units"][0]["lessons"][1]["goals"][0]["skills"][0]["prerequisites"] == ["u1-l1-s1"]


def test_api_outline_needs_a_pdf():
    r = client.post("/api/outline/", headers=HEADERS, files={"file": ("livre.docx", b"PK..")})
    assert r.status_code == 400


def test_api_parse_lesson_errors():
    data = {"outline": "{\"units\": 3}", "unit_index": "1", "lesson_index": "1"}
    r = client.post("/api/parse-lesson/", headers=HEADERS, files={"file": ("l.pdf", book(4))}, data=data)
    assert r.status_code == 422
    data["outline"] = EXAMPLE.model_dump_json()
    data["lesson_index"] = "7"
    r = client.post("/api/parse-lesson/", headers=HEADERS, files={"file": ("l.pdf", book(20))}, data=data)
    assert r.status_code == 400 and "n'existe pas" in r.text


# --- Ligne de commande --auto ----------------------------------------------------------------
@pytest.fixture
def in_tmp(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("PAUSE_BETWEEN_CALLS_SECONDS", "0")
    (tmp_path / "livre.pdf").write_bytes(book(60))
    return tmp_path


def test_cli_plan_only_writes_the_plan(in_tmp, capsys):
    assert cli(["livre.pdf", "--auto", "--provider", "fake", "--plan-only"]) == 0
    saved = json.loads((in_tmp / "resultats" / "livre_plan.json").read_text(encoding="utf-8"))
    assert len(saved["units"][0]["lessons"]) == 3
    assert '--plan "resultats' in capsys.readouterr().out


def test_cli_stops_when_the_plan_has_errors(in_tmp, capsys):
    (in_tmp / "plan.json").write_text(plan(("a", 5, 10), ("b", 8, 12)).model_dump_json(), encoding="utf-8")
    assert cli(["livre.pdf", "--auto", "--provider", "fake", "--plan", "plan.json"]) == 4
    assert "PAGE_OVERLAP" in capsys.readouterr().out


def test_cli_auto_with_corrected_plan(in_tmp, capsys):
    (in_tmp / "plan.json").write_text(EXAMPLE.model_dump_json(), encoding="utf-8")
    assert cli(["livre.pdf", "--auto", "--provider", "fake", "--plan", "plan.json", "--units", "1"]) == 0
    out = capsys.readouterr().out
    assert "leçon 3/3" in out and "Skills : 12" in out
    result = json.loads((in_tmp / "resultats" / "livre_fake_livre_unites_1.json").read_text(encoding="utf-8"))
    assert [l["title"] for l in result["draft"]["units"][0]["lessons"]] == ["العدّ إلى 5", "كتابة الأرقام", "الجمع حتى 5"]


def test_cli_rejects_unknown_unit(in_tmp, capsys):
    (in_tmp / "plan.json").write_text(EXAMPLE.model_dump_json(), encoding="utf-8")
    assert cli(["livre.pdf", "--auto", "--provider", "fake", "--plan", "plan.json", "--units", "2"]) == 1


# --- Reprise après un arrêt (quota épuisé, coupure) ---------------------------------------------
def test_cli_saves_each_lesson_and_resumes(in_tmp, capsys):
    (in_tmp / "plan.json").write_text(EXAMPLE.model_dump_json(), encoding="utf-8")
    args = ["livre.pdf", "--auto", "--provider", "fake", "--plan", "plan.json", "--units", "1"]
    assert cli(args) == 0
    saved = sorted(p.name for p in (in_tmp / "resultats" / "brouillons" / "livre_fake").iterdir())
    assert saved == ["u1-l1.json", "u1-l2.json", "u1-l3.json"]
    capsys.readouterr()
    assert cli(args) == 0  # 2e fois : rien n'est redemandé à l'IA
    out = capsys.readouterr().out
    assert out.count("déjà faite") == 3 and "Skills : 12" in out
    assert cli(args + ["--fresh"]) == 0  # --fresh : on refait tout
    assert "déjà faite" not in capsys.readouterr().out


def test_cli_redoes_a_lesson_when_the_plan_changed(in_tmp, capsys):
    (in_tmp / "plan.json").write_text(EXAMPLE.model_dump_json(), encoding="utf-8")
    args = ["livre.pdf", "--auto", "--provider", "fake", "--plan", "plan.json", "--units", "1"]
    assert cli(args) == 0
    changed = EXAMPLE.model_copy(deep=True)
    changed.units[0].lessons[2].title = "الجمع"  # l'admin a corrigé le titre de la leçon 3
    (in_tmp / "plan.json").write_text(changed.model_dump_json(), encoding="utf-8")
    capsys.readouterr()
    assert cli(args) == 0
    out = capsys.readouterr().out
    assert out.count("déjà faite") == 2 and "a changé : on la refait" in out


def test_cli_stops_at_once_when_quota_is_exhausted_then_resumes(in_tmp, capsys, monkeypatch):
    from app.providers import QuotaExhaustedError
    from app.providers.fake import FakeProvider

    (in_tmp / "plan.json").write_text(EXAMPLE.model_dump_json(), encoding="utf-8")
    args = ["livre.pdf", "--auto", "--provider", "fake", "--plan", "plan.json", "--units", "1"]
    real = FakeProvider.generate
    calls = {"n": 0}

    def generate(self, doc, instructions, repair=None):
        calls["n"] += 1
        if calls["n"] == 2:  # la 2e leçon tombe sur le quota du jour
            raise QuotaExhaustedError("Gemini : quota du jour épuisé.")
        return real(self, doc, instructions, repair)

    monkeypatch.setattr(FakeProvider, "generate", generate)
    assert cli(args) == 5
    out = capsys.readouterr().out
    assert "Quota de l'IA épuisé" in out and "Leçons enregistrées : 1/3" in out
    assert "leçon 3/3" not in out  # on n'a pas essayé les leçons suivantes
    assert not (in_tmp / "resultats" / "livre_fake_livre_unites_1.json").exists()

    monkeypatch.setattr(FakeProvider, "generate", real)  # le lendemain : le quota est revenu
    assert cli(args) == 0
    out = capsys.readouterr().out
    assert out.count("déjà faite") == 1 and "Skills : 12" in out
