"""Étape 1 : l'IA lit tout le livre et renvoie directement les unités avec leurs leçons."""
import io
import json
from types import SimpleNamespace

from pypdf import PdfWriter

from app import pipeline
from app.outline import check_outline, run_outline
from app.providers.base import AIProvider
from app.schemas import Outline


def blank_pdf(pages: int) -> bytes:
    writer = PdfWriter()
    for _ in range(pages):
        writer.add_blank_page(width=100, height=100)
    out = io.BytesIO()
    writer.write(out)
    return out.getvalue()


def lessons(*pages):
    return [{"title": f"درس {p}", "start_page": p, "end_page": p + 1} for p in pages]


def unit(title, *pages, basis="theme"):
    return {"title": title, "central_question": "؟", "origin": "proposed", "basis": basis, "lessons": lessons(*pages)}


class Scripted(AIProvider):
    name, model = "scripted", "test"

    def __init__(self, *answers):
        self.answers, self.calls = list(answers), []

    def generate(self, doc, instructions, repair=None):
        self.calls.append(SimpleNamespace(doc=doc, instructions=instructions))
        return json.dumps(self.answers.pop(0), ensure_ascii=False)


def test_one_call_returns_units_with_their_lessons():
    plan = {"units": [unit("الوضعيات النسبية", 5, 7, 9), unit("الأعداد", 13, 15, 17, 19)],
            "breaks": [{"title": "الحصيلة 1", "start_page": 11, "end_page": 12}]}
    ai = Scripted(plan)
    result = run_outline(blank_pdf(30), "livre.pdf", ai, min_lessons=3, max_lessons=10)
    assert len(ai.calls) == 1  # un seul appel : plus de 2e appel pour couper les unités
    instructions = ai.calls[0].instructions
    assert instructions.startswith("TASK: OUTLINE")
    for words in ("DIVIDE THE BOOK INTO UNITS", "3 to 10 lessons", '"تابع"', "CONSECUTIVE"):
        assert words in instructions
    assert len(ai.calls[0].doc.pages) == 30  # chaque page étiquetée
    assert [u.title for u in result.outline.units] == ["الوضعيات النسبية", "الأعداد"]
    assert result.issues == []  # les pages 11-12 sont un bilan connu : pas de PAGE_GAP


def test_unit_sizes_are_checked():
    plan = Outline.model_validate({"units": [unit("كبيرة", *range(5, 29, 2)), unit("صغيرة", 29, 31)]})
    found = [i.code for i in check_outline(plan, 40, max_lessons=10, min_lessons=3)]
    assert found == ["UNIT_TOO_LARGE", "UNIT_TOO_SMALL"]


def test_lessons_out_of_order_are_reported():
    plan = Outline.model_validate({"units": [unit("أ", 5, 9), unit("ب", 7)]})
    assert "PAGE_OVERLAP" in [i.code for i in check_outline(plan, 20)]


def test_gap_not_covered_by_breaks_is_still_reported():
    plan = Outline.model_validate({
        "units": [{"title": "u", "lessons": lessons(5, 9)}],
        "breaks": [{"title": "الحصيلة", "start_page": 7, "end_page": 7}],
    })
    assert [i.code for i in check_outline(plan, 20)] == ["PAGE_GAP"]  # la page 8 manque


def test_heavy_book_pages_are_sent_as_images(monkeypatch):
    monkeypatch.setattr(pipeline, "MAX_LABELED_BYTES", 10)  # « trop lourd » en PDF
    monkeypatch.setattr(pipeline, "render_pages_jpeg", lambda data: [b"\xff\xd8x"] * 3)
    doc = pipeline.labeled_pdf_document("b.pdf", blank_pdf(5), 2, 4)
    assert doc.pages_mime == "image/jpeg" and len(doc.pages) == 3 and doc.first_page == 2