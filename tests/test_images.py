"""Images : l'IA signale les chunks qui ont besoin d'une image, l'admin l'ajoute."""
import pytest

from app.pipeline import validate_draft_data
from app.prompts import build_lesson_instructions
from app.rules import answer_from_picture, mentions_picture
from app.schemas import Chunk

PRACTICE = "units.0.lessons.0.goals.0.skills.0.chunks.2"


def codes(result):
    return [i.code for i in result.issues]


def chunk(sample):
    return sample["units"][0]["lessons"][0]["goals"][0]["skills"][0]["chunks"][2]


def test_sample_needs_no_image(sample):
    assert codes(validate_draft_data(sample)) == []


def test_chunk_that_needs_an_image_is_listed_for_the_admin(sample):
    c = chunk(sample)
    c["needs_image"], c["image_note"], c["source_page"] = True, "page 8 : une fille assise sur un lit", 8
    result = validate_draft_data(sample)
    assert result.valid is True  # un simple avertissement : n'empêche pas l'enregistrement
    # l'exercice parle de positions : sa bonne réponse est aussi à vérifier sur la page
    assert codes(result) == ["IMAGE_NEEDED", "ANSWER_FROM_PICTURE"]
    assert "une fille assise sur un lit" in result.issues[0].message
    assert "page 8" in result.issues[1].message


def test_image_without_note_points_to_the_page(sample):
    c = chunk(sample)
    c["needs_image"], c["source_page"] = True, 12
    assert "voir page 12" in validate_draft_data(sample).issues[0].message


def test_text_about_a_picture_without_image_is_reported(sample):
    chunk(sample)["content"] = "أَيْنَ الْمِحْفَظَةُ فِي الصُّورَةِ؟"
    assert codes(validate_draft_data(sample)) == ["IMAGE_REFERENCE", "ANSWER_FROM_PICTURE"]


@pytest.mark.parametrize("text, expected", [
    ("أين توجد المحفظة في الصورة؟", True),
    ("لاحظ الرسم جيدا", True),
    ("للصورة", True),
    ("Regarde le dessin.", True),
    ("أجب بصورة صحيحة", False),  # « بصورة » = « de façon » : pas une image
    ("العصافير فوق الشجرة", False),
    ("Le livre est sur la table.", False),
])
def test_mentions_picture(text, expected):
    assert mentions_picture(text) is expected


def test_prompt_defines_each_chunk_type_and_pictures():
    text = build_lesson_instructions("ar", "u", ["l1"], 1, 7, 8, True)
    for expected in ('"rule": the KNOWLEDGE', '"example": a SOLVED case', "needs_image=true", "image_note",
                     "must not contain", "can be false", "book's own cards"):
        assert expected in text


# --- Réponses à vérifier sur l'image (ANSWER_FROM_PICTURE) ----------------------------------------
def practice(question, options, needs_image=True, page=24):
    return Chunk.model_validate({
        "chunk_type": "practice", "content": question, "order_index": 1, "origin": "extracted",
        "source_page": page, "needs_image": needs_image, "image_note": "page" if needs_image else None,
        "options": [{"id": "abc"[i], "text": t, "is_correct": i == 0,
                     "error_category": None if i == 0 else "concept", "feedback": "f"}
                    for i, t in enumerate(options)],
    })


@pytest.mark.parametrize("question, options, kind", [
    ("أَعُدُّ الطُّيُورَ فِي الصُّورَةِ، كَمْ عَدَدُهَا؟", ["6", "5"], "comptage"),
    ("أَيُّ الْمَجْمُوعَاتِ فِيهَا 4 عَنَاصِرَ؟", ["مَجْمُوعَةُ الْهَوَاتِفِ", "مَجْمُوعَةُ الْأَطْفَالِ"], "comptage"),
    ("اخْتَرِ الْبِطَاقَةَ الصَّحِيحَةَ", ["السَّيَّارَةُ عَلَى يَمِينِ الْكُرَةِ", "الْكُرَةُ عَلَى يَمِينِ السَّيَّارَةِ"], "position"),
    ("أَيُّ الشَّجَرَتَيْنِ أَطْوَلُ؟", ["الشَّجَرَةُ الْخَضْرَاءُ", "النَّخْلَةُ"], "taille / comparaison"),
    ("Combien de canards vois-tu ?", ["6", "5"], "comptage"),
])
def test_answer_read_on_a_picture_is_flagged(question, options, kind):
    assert kind in answer_from_picture(practice(question, options))


def test_no_flag_without_picture_or_without_visual_reading():
    assert answer_from_picture(practice("3 ... 2 : مَا هِيَ الْإِشَارَةُ؟", [">", "<"], needs_image=False)) is None
    assert answer_from_picture(practice("مَا اسْمُ هَذَا الْحَيَوَانِ؟", ["قِطٌّ", "كَلْبٌ"])) is None


def test_flag_gives_the_page_and_the_answer_marked_correct(sample):
    c = sample["units"][0]["lessons"][0]["goals"][0]["skills"][0]["chunks"][2]
    c["needs_image"], c["image_note"], c["source_page"] = True, "page 24 : oiseaux", 24
    c["content"] = "أَعُدُّ الطُّيُورَ فِي الصُّورَةِ، كَمْ عَدَدُهَا؟"
    for i, o in enumerate(c["options"]):
        o["text"] = ["6", "5", "4"][i] if i < 3 else o["text"]
    flag = [i for i in validate_draft_data(sample).issues if i.code == "ANSWER_FROM_PICTURE"][0]
    assert flag.level == "warning" and "page 24" in flag.message and "comptage" in flag.message
    correct = next(o["text"] for o in c["options"] if o["is_correct"])
    assert f"« {correct} »" in flag.message


# --- Lien chunk -> image (champ « image », rempli par l'admin) ------------------------------------
def test_linked_image_removes_the_reminder(sample):
    c = chunk(sample)
    c["needs_image"], c["source_page"], c["image_note"] = True, 12, "page 12"
    assert "IMAGE_NEEDED" in codes(validate_draft_data(sample))
    c["image"] = "images/u1-l1-g1-s1-c3.png"
    assert "IMAGE_NEEDED" not in codes(validate_draft_data(sample))


@pytest.mark.parametrize("bad", ["../secret.png", "images/a b.png", "images/x.gif", "javascript:alert(1)",
                                 "https://x/a b.png", "ftp://x/y.png"])
def test_bad_image_links_are_refused(sample, bad):
    chunk(sample)["image"] = bad
    assert validate_draft_data(sample).valid is False


@pytest.mark.parametrize("good", ["images/u1-l1-s1-c3.png", "https://cms.fatin.mr/media/chunks/u1-l1-s1-c3.png"])
def test_image_link_is_a_file_or_a_web_address(sample, good):
    chunk(sample)["image"] = good
    assert validate_draft_data(sample).valid is True


def test_ai_never_sees_or_fills_the_image_field(sample):
    import json
    from app.lesson import rename_refs
    from app.schemas import Lesson
    assert '"image":' not in json.dumps(Lesson.model_json_schema())
    lesson = sample["units"][0]["lessons"][0]
    lesson["goals"][0]["skills"][0]["chunks"][0]["image"] = "images/invente.png"  # l'IA l'aurait inventé
    cleaned = rename_refs(Lesson.model_validate(lesson), 1, 1)
    assert all(c.image is None for g in cleaned.goals for s in g.skills for c in s.chunks)


# --- Nom fixe des chunks (ref) ----------------------------------------------------------------------
def test_our_service_names_every_chunk_and_the_ai_cannot():
    import json
    from app.lesson import rename_refs
    from app.schemas import Lesson
    assert '"ref"' in json.dumps(Lesson.model_json_schema())  # les skills ont un ref...
    chunk_schema = Lesson.model_json_schema()["$defs"]["Chunk"]["properties"]
    assert "ref" not in chunk_schema and "image" not in chunk_schema  # ... mais l'IA ne voit pas celui des chunks
    import copy
    from tests.conftest import SAMPLE
    lesson = json.loads(SAMPLE.read_text(encoding="utf-8"))["units"][0]["lessons"][0]
    lesson["goals"][0]["skills"][0]["chunks"][0]["ref"] = "invente"
    named = rename_refs(Lesson.model_validate(lesson), 2, 3)
    refs = [c.ref for g in named.goals for s in g.skills for c in s.chunks]
    assert refs[:3] == ["u2-l3-s1-c1", "u2-l3-s1-c2", "u2-l3-s1-c3"]
    assert len(set(refs)) == len(refs) and all(len(r) <= 30 for r in refs)


def test_warning_on_a_chunk_carries_its_name(sample):
    c = chunk(sample)
    c["ref"], c["needs_image"], c["source_page"] = "u1-l1-s1-c3", True, 24
    issue = [i for i in validate_draft_data(sample).issues if i.code == "IMAGE_NEEDED"][0]
    assert issue.ref == "u1-l1-s1-c3"


def test_name_stays_after_the_admin_deletes_a_chunk_before_it(sample):
    skill = sample["units"][0]["lessons"][0]["goals"][0]["skills"][0]
    for i, c in enumerate(skill["chunks"], start=1):
        c["ref"] = f"u1-l1-s1-c{i}"
    skill["chunks"][2].update(needs_image=True, source_page=24)
    del skill["chunks"][0]  # l'admin supprime le 1er chunk : le chunk à image est maintenant 2e
    for i, c in enumerate(skill["chunks"], start=1):
        c["order_index"] = i
    issue = [i for i in validate_draft_data(sample).issues if i.code == "IMAGE_NEEDED"][0]
    assert issue.path.endswith("chunks[1]") and issue.ref == "u1-l1-s1-c3"  # la position change, le nom non


def test_duplicate_chunk_names_are_an_error(sample):
    chunks = sample["units"][0]["lessons"][0]["goals"][0]["skills"][0]["chunks"]
    chunks[0]["ref"] = chunks[1]["ref"] = "u1-l1-s1-c1"
    assert "CHUNK_REF_DUPLICATE" in codes(validate_draft_data(sample))


# --- pdfium : un seul appel à la fois (serveur avec plusieurs requêtes) -----------------------------
def test_page_images_are_right_when_many_requests_arrive_together():
    """Sans le verrou, ce test faisait planter Python (pdfium appelé par 8 requêtes à la fois)."""
    import io
    from concurrent.futures import ThreadPoolExecutor
    pytest.importorskip("reportlab")
    from reportlab.lib.pagesizes import A4
    from reportlab.pdfgen import canvas
    from app.pipeline import render_pages_jpeg
    out = io.BytesIO()
    page = canvas.Canvas(out, pagesize=A4)
    for p in range(4):  # des pages chargées (texte + dessins), comme un manuel
        for i in range(60):
            page.circle(50 + (i * 37) % 500, 80 + (i * 53) % 700, 10 + i % 25)
            page.drawString(40, 40 + i * 12, f"ligne {i} page {p} " * 6)
        page.showPage()
    page.save()
    pdf = out.getvalue()
    with ThreadPoolExecutor(8) as pool:
        counts = list(pool.map(lambda _: len(render_pages_jpeg(pdf) or []), range(24)))
    assert counts == [4] * 24
