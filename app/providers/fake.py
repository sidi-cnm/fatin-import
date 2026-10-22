"""
Faux fournisseur : répond sans appeler aucune IA.

Sert à développer et tester sans clé API, et aux développeurs FATIN pour construire
l'écran de relecture avec une vraie réponse. Il lit la 1re ligne de la consigne (TASK: ...) :

  - UNIT    : renvoie l'exemple samples/u01_l01.json
  - OUTLINE : le repérage d'un livre sans unités ni bilans, avec jusqu'à 3 leçons
  - UNITS   : chaque groupe devient une unité (coupé en parts de MAX leçons s'il le faut)
  - LESSON  : la leçon de l'exemple (U01-L01)
  - LINKS   : la 1re skill de chaque leçon a besoin de la 1re skill de la leçon précédente
"""
import io
import json
import re
from pathlib import Path
from typing import Optional

from .base import AIProvider, SourceDocument

SAMPLE = Path(__file__).resolve().parents[2] / "samples" / "u01_l01.json"


class FakeProvider(AIProvider):
    name = "fake"
    model = "sample-u01-l01"

    def __init__(self, subject_id: int = 0):
        self._subject_id = subject_id

    def generate(self, doc: SourceDocument, instructions: str, repair: Optional[str] = None) -> str:
        task = instructions.lstrip().split("\n", 1)[0].removeprefix("TASK:").strip()
        sample = json.loads(SAMPLE.read_text(encoding="utf-8"))
        if task == "OUTLINE":
            return json.dumps(self._outline(doc), ensure_ascii=False)
        if task == "UNITS":
            return json.dumps(self._units(doc.text or ""), ensure_ascii=False)
        if task == "LESSON":
            return json.dumps(sample["units"][0]["lessons"][0], ensure_ascii=False)
        if task == "LINKS":
            return json.dumps(self._links(doc.text or ""), ensure_ascii=False)
        sample["subject"] = self._subject_id
        sample["source_file"] = doc.filename
        return json.dumps(sample, ensure_ascii=False)

    @staticmethod
    def _outline(doc: SourceDocument) -> dict:
        if doc.pages:
            total = len(doc.pages)
        else:
            from pypdf import PdfReader

            total = len(PdfReader(io.BytesIO(doc.data)).pages)
        count = max(1, min(3, total // 2))
        size = total // count
        lessons = []
        for i in range(count):
            start = doc.first_page + i * size
            end = doc.first_page + total - 1 if i == count - 1 else start + size - 1
            lessons.append({"title": f"الدرس {i + 1}", "start_page": start, "end_page": end})
        unit = {"title": "الوحدة الأولى", "central_question": None, "origin": "proposed", "basis": "theme",
                  "lessons": lessons}
        return {"units": [unit], "breaks": []}

    @staticmethod
    def _units(overview: str) -> dict:
        groups = []
        for match in re.finditer(r"GROUP (\d+) \((\d+) lessons\)(?: — MUST BE CUT \(max (\d+))?", overview):
            number, size = int(match.group(1)), int(match.group(2))
            step = int(match.group(3) or size)
            units = [
                {"first_lesson": start, "title": f"الوحدة {number}-{k}", "central_question": "؟"}
                for k, start in enumerate(range(1, size + 1, step), start=1)
            ]
            groups.append({"group": number, "units": units})
        return {"groups": groups}

    @staticmethod
    def _links(overview: str) -> dict:
        first_skill_of_lesson: list[str] = []
        current_lesson = None
        for line in overview.splitlines():
            match = re.match(r"\s+(u\d+-l\d+)-s\d+ ", line)
            if match and match.group(1) != current_lesson:
                current_lesson = match.group(1)
                first_skill_of_lesson.append(line.split()[0])
        links = [
            {"skill": skill, "prerequisites": [first_skill_of_lesson[i - 1]]}
            for i, skill in enumerate(first_skill_of_lesson)
            if i > 0
        ]
        return {"links": links}
