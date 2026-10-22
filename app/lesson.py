"""
Découpage automatique, ÉTAPE 2 : une leçon à la fois.

Pour la leçon demandée :
  1. pypdf découpe SES pages dans le livre (chaque page garde son numéro dans le livre) ;
  2. l'IA détaille la leçon (2 objectifs, skills, chunks, réponses) ;
  3. le FORMAT est vérifié (1 seconde chance si raté) ;
  4. les identifiants sont renommés pour être uniques dans tout le livre (s1 -> u1-l3-s1) ;
  5. les RÈGLES sont vérifiées ; s'il reste des erreurs, l'IA a 1 chance de les corriger,
     pour CETTE leçon seulement. On garde la meilleure des deux versions.
"""
import hashlib
import time

from .pipeline import (
    Progress,
    UnsupportedFile,
    ask_for_model,
    generate_with_retry,
    labeled_pdf_document,
    to_model,
)
from .prompts import build_lesson_instructions, build_rules_repair_message
from .providers import AIProvider
from .rules import check_draft
from .schemas import ImportDraft, Issue, Lesson, LessonResponse, Meta, Outline, Unit


def lesson_ref(unit_index: int, lesson_index: int) -> str:
    return f"u{unit_index}-l{lesson_index}"


def run_lesson(
    data: bytes,
    filename: str,
    outline: Outline,
    unit_index: int,
    lesson_index: int,
    provider: AIProvider,
    language: str = "ar",
    stage: str = "",
    level: str = "",
    subject_name: str = "",
    retry_attempts: int = 4,
    retry_wait_seconds: float = 15,
    fix_rules: bool = True,
    progress: Progress = None,
) -> LessonResponse:
    """unit_index et lesson_index commencent à 1 (1re unité du plan, 1re leçon de l'unité)."""
    started = time.monotonic()
    if not 1 <= unit_index <= len(outline.units):
        raise UnsupportedFile(f"L'unité {unit_index} n'existe pas dans le plan ({len(outline.units)} unités).")
    unit = outline.units[unit_index - 1]
    if not 1 <= lesson_index <= len(unit.lessons):
        raise UnsupportedFile(
            f"La leçon {lesson_index} n'existe pas dans l'unité {unit_index} ({len(unit.lessons)} leçons)."
        )
    planned = unit.lessons[lesson_index - 1]

    # 1. seulement les pages de la leçon, avec leurs numéros dans le livre
    doc = labeled_pdf_document(filename, data, planned.start_page, planned.end_page)

    # 2-3. l'IA détaille la leçon ; titre et position sont imposés par le plan
    instructions = build_lesson_instructions(
        language, unit.title, [l.title for l in unit.lessons], lesson_index,
        planned.start_page, planned.end_page, pages_labeled=bool(doc.pages),
        stage=stage, level=level, subject_name=subject_name,
    )
    force = {"ref": "l1", "title": planned.title, "order_index": lesson_index, "source_page": planned.start_page}
    lesson, raw, repaired = ask_for_model(
        provider, doc, instructions, Lesson, force,
        retry_attempts=retry_attempts, retry_wait_seconds=retry_wait_seconds, progress=progress,
    )

    # 4. identifiants uniques dans tout le livre
    lesson = rename_refs(lesson, unit_index, lesson_index)

    # 5. règles pédagogiques ; 1 chance de correction s'il reste des erreurs
    issues = check_lesson(lesson, language)
    errors = [i for i in issues if i.level == "error"]
    if errors and fix_rules:
        if progress:
            progress(f"{len(errors)} règle(s) non respectée(s) : l'IA corrige cette leçon...")
        problems = [f"[{i.code}] {i.path}: {i.message}" for i in errors]
        raw2 = generate_with_retry(
            provider, doc, instructions, build_rules_repair_message(raw, problems),
            retry_attempts, retry_wait_seconds, progress,
        )
        fixed, format_errors = to_model(raw2, Lesson, force)
        if fixed is not None:
            fixed = rename_refs(fixed, unit_index, lesson_index)
            fixed_issues = check_lesson(fixed, language)
            if _error_count(fixed_issues) < len(errors):  # on garde la meilleure version
                lesson, issues = fixed, fixed_issues
        repaired = True

    return LessonResponse(
        unit_index=unit_index,
        lesson_index=lesson_index,
        lesson=lesson,
        issues=issues,
        meta=Meta(
            provider=provider.name,
            model=provider.model,
            duration_seconds=round(time.monotonic() - started, 2),
            repaired=repaired,
        ),
    )


def lesson_fingerprint(
    outline: Outline,
    unit_index: int,
    lesson_index: int,
    model: str,
    language: str = "ar",
    stage: str = "",
    level: str = "",
    subject_name: str = "",
) -> str:
    """Empreinte d'une leçon à détailler : le modèle + la consigne exacte envoyée à l'IA.

    On y met le titre et les pages de CETTE leçon, le titre de l'unité et toutes les règles du
    prompt. Si l'un d'eux change (plan corrigé, prompt amélioré, autre modèle), l'empreinte
    change : une leçon déjà enregistrée n'est reprise que si elle a été faite dans les MÊMES
    conditions. Corriger le titre d'une AUTRE leçon ne force pas à refaire celle-ci.
    """
    unit = outline.units[unit_index - 1]
    planned = unit.lessons[lesson_index - 1]
    instructions = build_lesson_instructions(
        language, unit.title, [planned.title], 1,
        planned.start_page, planned.end_page, pages_labeled=True,
        stage=stage, level=level, subject_name=subject_name,
    )
    return hashlib.sha256(f"{model}\n{instructions}".encode("utf-8")).hexdigest()[:16]


def rename_refs(lesson: Lesson, unit_index: int, lesson_index: int) -> Lesson:
    """Rend les identifiants uniques dans tout le livre.

    Chaque leçon numérote ses skills à partir de 1 (s1, s2...). On les renumérote dans
    l'ordre et on ajoute la position de la leçon devant : s1 de la leçon 3 de l'unité 1
    devient u1-l3-s1. Les prérequis internes à la leçon suivent le même renommage ;
    un prérequis qui vise une autre leçon (déjà renommé) reste tel quel.
    """
    prefix = lesson_ref(unit_index, lesson_index)
    data = lesson.model_dump()
    skills = [skill for goal in data["goals"] for skill in goal["skills"]]
    mapping: dict[str, str] = {}
    for number, skill in enumerate(skills, start=1):
        mapping.setdefault(skill["ref"], f"{prefix}-s{number}")
        skill["ref"] = f"{prefix}-s{number}"
    for skill in skills:
        skill["prerequisites"] = list(dict.fromkeys(mapping.get(p, p) for p in skill["prerequisites"]))
    data["ref"] = prefix
    for number, skill in enumerate(skills, start=1):
        for c, chunk in enumerate(skill["chunks"], start=1):
            chunk["ref"] = f"{prefix}-s{number}-c{c}"  # nom fixe : u1-l2-s1-c3
            chunk["image"] = None  # le lien vers une image vient de l'admin, jamais de l'IA
    return Lesson.model_validate(data)


def check_lesson(lesson: Lesson, language: str) -> list[Issue]:
    """Les règles habituelles (rules.py), appliquées à une seule leçon.

    Les chemins sont donnés à partir de la leçon : goals[1].skills[0]...
    """
    unit = Unit(ref="u", title="-", order_index=1, lessons=[lesson])
    draft = ImportDraft(subject=0, source_file="-", language=language, units=[unit])
    prefix = "units[0].lessons[0]."
    issues = []
    for issue in check_draft(draft):
        if issue.path.startswith(prefix):
            issue = issue.model_copy(update={"path": issue.path[len(prefix):]})
        issues.append(issue)
    return issues


def _error_count(issues: list[Issue]) -> int:
    return sum(1 for i in issues if i.level == "error")
