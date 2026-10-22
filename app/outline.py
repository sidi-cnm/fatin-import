"""
Découpage automatique, ÉTAPE 1 : le plan du livre.

  1a. REPÉRAGE (IA) : on envoie le livre entier, chaque page étiquetée « === PAGE n === ».
      L'IA renvoie seulement ce qu'elle VOIT : les leçons, les titres d'unités imprimés
      (s'il y en a) et les pages de bilan. Réponse courte, donc fiable.
  1b. UNITÉS (code + petit appel IA) : units.py applique les 3 règles, identiques pour tous
      les livres. L'IA n'intervient que pour couper par thème et nommer (règle 3), à partir
      des seuls titres des leçons.
  1c. VÉRIFICATION (code) : check_outline signale pages impossibles, chevauchements, oublis,
      unités trop grosses, titres en double. L'admin corrige le plan AVANT l'étape 2.
"""
import time
from collections import Counter

from .pipeline import Progress, ask_for_model, count_pdf_pages, labeled_pdf_document, read_bookmarks
from .prompts import build_outline_instructions
from .providers import AIProvider
from .schemas import Issue, Meta, Outline, OutlineResponse

# Pour le plan, le haut de chaque page suffit (titres) : on limite le texte OCR de Mistral.
OUTLINE_EXCERPT_CHARS = 1200
# Au-delà, la leçon risque d'être trop longue pour une seule réponse de l'IA.
MAX_LESSON_PAGES = 20


def run_outline(
    data: bytes,
    filename: str,
    provider: AIProvider,
    language: str = "ar",
    stage: str = "",
    level: str = "",
    subject_name: str = "",
    retry_attempts: int = 4,
    retry_wait_seconds: float = 15,
    min_lessons: int = 3,
    max_lessons: int = 10,
    progress: Progress = None,
) -> OutlineResponse:
    started = time.monotonic()
    total = count_pdf_pages(data)

    # 1a. tout le livre, chaque page étiquetée ; l'IA renvoie directement unités + leçons + bilans
    doc = labeled_pdf_document(filename, data, 1, total)
    doc.excerpt_chars = OUTLINE_EXCERPT_CHARS
    instructions = build_outline_instructions(
        language, total, pages_labeled=bool(doc.pages), bookmarks=read_bookmarks(data),
        stage=stage, level=level, subject_name=subject_name,
        min_lessons=min_lessons, max_lessons=max_lessons,
    )
    outline, _, repaired = ask_for_model(
        provider, doc, instructions, Outline,
        retry_attempts=retry_attempts, retry_wait_seconds=retry_wait_seconds, progress=progress,
    )
    outline = outline.model_copy(update={"breaks": sorted(outline.breaks, key=lambda b: b.start_page)})

    # 1b. vérification
    return OutlineResponse(
        outline=outline,
        total_pages=total,
        issues=check_outline(outline, total, max_lessons, min_lessons),
        meta=Meta(
            provider=provider.name,
            model=provider.model,
            duration_seconds=round(time.monotonic() - started, 2),
            repaired=repaired,
        ),
    )


def check_outline(outline: Outline, total_pages: int, max_lessons: int = 10, min_lessons: int = 3) -> list[Issue]:
    """Vérifie le plan, sans IA. `error` = à corriger avant l'étape 2 ; `warning` = à regarder."""
    issues: list[Issue] = []

    def err(code: str, path: str, message: str) -> None:
        issues.append(Issue(code=code, level="error", path=path, message=message))

    def warn(code: str, path: str, message: str) -> None:
        issues.append(Issue(code=code, level="warning", path=path, message=message))

    if not outline.units:
        err("OUTLINE_EMPTY", "units", "Le plan ne contient aucune unité.")

    break_pages = {p for b in outline.breaks for p in range(b.start_page, b.end_page + 1)}
    titles = Counter(l.title.strip() for u in outline.units for l in u.lessons)
    seen_titles: set[str] = set()
    previous = None  # (titre, fin) de la leçon précédente, dans l'ordre du livre
    for ui, unit in enumerate(outline.units):
        up = f"units[{ui}]"
        if not unit.lessons:
            err("UNIT_NO_LESSONS", up, f"L'unité « {unit.title} » n'a aucune leçon.")
        elif len(unit.lessons) > max_lessons:
            warn("UNIT_TOO_LARGE", up,
                 f"L'unité « {unit.title} » a {len(unit.lessons)} leçons (maximum conseillé : {max_lessons}).")
        elif len(unit.lessons) < min_lessons and len(outline.units) > 1:
            warn("UNIT_TOO_SMALL", up,
                 f"L'unité « {unit.title} » a {len(unit.lessons)} leçon(s) (minimum conseillé : {min_lessons}).")    
        for li, lesson in enumerate(unit.lessons):
            lp = f"{up}.lessons[{li}]"
            name = f"« {lesson.title} » (pages {lesson.start_page}-{lesson.end_page})"
            if titles[lesson.title.strip()] > 1 and lesson.title.strip() in seen_titles:
                warn("LESSON_DUPLICATE_TITLE", lp,
                     f"Le titre de la leçon {name} apparaît {titles[lesson.title.strip()]} fois : "
                     f"vérifier que ce sont bien des leçons différentes.")
            seen_titles.add(lesson.title.strip())
            if lesson.start_page > lesson.end_page:
                err("PAGE_ORDER", lp, f"La leçon {name} finit avant de commencer.")
                continue
            if lesson.end_page > total_pages:
                err("PAGE_OUT_OF_RANGE", lp, f"La leçon {name} dépasse la fin du livre ({total_pages} pages).")
                continue
            size = lesson.end_page - lesson.start_page + 1
            if size > MAX_LESSON_PAGES:
                warn("LESSON_TOO_LONG", lp, f"La leçon {name} fait {size} pages : vérifier le découpage.")
            if previous:
                prev_title, prev_end = previous
                # partager UNE page (fin d'une leçon = début de la suivante) est normal
                if lesson.start_page < prev_end:
                    err(
                        "PAGE_OVERLAP", lp,
                        f"La leçon {name} commence avant la fin de « {prev_title} » (page {prev_end}).",
                    )
                elif lesson.start_page > prev_end + 1:
                    missing = set(range(prev_end + 1, lesson.start_page))
                    if not missing <= break_pages:  # des pages de bilan connues ne sont pas un oubli
                        gap = f"{prev_end + 1}-{lesson.start_page - 1}"
                        warn(
                            "PAGE_GAP", lp,
                            f"Les pages {gap} ne sont dans aucune leçon (avant {name}). Normal si ce sont des "
                            f"pages d'introduction ou d'évaluation.",
                        )
            previous = (lesson.title, lesson.end_page)
    return issues


def outline_errors(issues: list[Issue]) -> list[Issue]:
    return [i for i in issues if i.level == "error"]
