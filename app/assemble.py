"""
Découpage automatique, ÉTAPES 3 et 4.

ÉTAPE 3 — les liens entre leçons (find_links)
  À l'étape 2, l'IA ne voyait qu'une leçon : elle n'a pas pu relier « additionner » (leçon 3)
  à « compter » (leçon 1). On lui envoie maintenant la LISTE des skills de tout le livre
  (noms seulement, pas le contenu) et elle répond : quelle skill a besoin de quelle autre.
  Réponse courte, donc fiable. Les liens incohérents sont écartés et signalés.

ÉTAPE 4 — l'assemblage (assemble), sans IA
  1. ranger chaque leçon dans son unité, dans l'ordre du plan ;
  2. vérifier que les identifiants sont uniques (s1 -> u1-l3-s1, déjà fait à l'étape 2) ;
  3. ajouter les liens de l'étape 3 ;
  4. revérifier TOUT le brouillon avec rules.py (boucles entre leçons, etc.) ;
  5. renvoyer UN brouillon, au même format que /api/parse-document/.
"""
import time
from typing import Optional

from .lesson import lesson_ref, rename_refs
from .pipeline import TEXT, PipelineError, Progress, ask_for_model
from .prompts import build_links_instructions
from .providers import AIProvider, ProviderError, SourceDocument
from .rules import check_draft
from .schemas import (
    AssembleRequest,
    ImportDraft,
    Issue,
    Lesson,
    Meta,
    Outline,
    ParseResponse,
    SkillLinks,
    Unit,
)


# --- Étape 3 : les liens entre leçons --------------------------------------------------
def skills_overview(draft: ImportDraft) -> str:
    """La liste envoyée à l'IA : unités, leçons, skills (ref, type, nom). Pas d'exercices."""
    lines = []
    for unit in draft.units:
        lines.append(f"UNIT {unit.ref}: {unit.title}")
        for lesson in unit.lessons:
            lines.append(f"  LESSON {lesson.ref}: {lesson.title}")
            for goal in lesson.goals:
                for skill in goal.skills:
                    lines.append(f"    {skill.ref} [{skill.skill_type}]: {skill.name}")
    return "\n".join(lines)


def find_links(
    draft: ImportDraft,
    provider: AIProvider,
    stage: str = "",
    level: str = "",
    subject_name: str = "",
    retry_attempts: int = 4,
    retry_wait_seconds: float = 15,
    progress: Progress = None,
) -> tuple[dict[str, list[str]], bool]:
    """Demande à l'IA les prérequis entre leçons. Renvoie ({skill: [prérequis]}, seconde_chance)."""
    overview = skills_overview(draft)
    doc = SourceDocument(filename="skills.txt", mime_type=TEXT, data=overview.encode("utf-8"), text=overview)
    instructions = build_links_instructions(draft.language, stage, level, subject_name)
    result, _, repaired = ask_for_model(
        provider, doc, instructions, SkillLinks,
        retry_attempts=retry_attempts, retry_wait_seconds=retry_wait_seconds, progress=progress,
    )
    links: dict[str, list[str]] = {}
    for link in result.links:
        links.setdefault(link.skill, []).extend(link.prerequisites)
    return links, repaired


def apply_links(draft: ImportDraft, links: dict[str, list[str]]) -> tuple[ImportDraft, list[Issue]]:
    """Ajoute les liens proposés. Écarte ceux qui visent une skill inconnue ou elle-même ;
    signale ceux qui pointent vers une leçon PLUS LOIN dans le livre (souvent à l'envers)."""
    issues: list[Issue] = []
    data = draft.model_dump()
    position: dict[str, int] = {}  # ref de skill -> rang de sa leçon dans le livre
    skills: dict[str, dict] = {}
    rank = 0
    for unit in data["units"]:
        for lesson in unit["lessons"]:
            rank += 1
            for goal in lesson["goals"]:
                for skill in goal["skills"]:
                    position[skill["ref"]] = rank
                    skills[skill["ref"]] = skill

    for ref, prerequisites in links.items():
        if ref not in skills:
            issues.append(Issue(code="LINK_UNKNOWN", level="warning", path=ref,
                                message=f"Lien ignoré : la skill « {ref} » n'existe pas."))
            continue
        target = skills[ref]
        for p in prerequisites:
            if p == ref or p in target["prerequisites"]:
                continue
            if p not in skills:
                issues.append(Issue(code="LINK_UNKNOWN", level="warning", path=ref,
                                    message=f"Lien ignoré : le prérequis « {p} » n'existe pas."))
                continue
            target["prerequisites"].append(p)
            if position[p] > position[ref]:
                issues.append(Issue(
                    code="PREREQ_LATER", level="warning", path=ref,
                    message=f"« {target['name']} » aurait besoin de « {skills[p]['name']} », qui est enseignée "
                            f"plus loin dans le livre. Lien à vérifier.",
                ))
    return ImportDraft.model_validate(data), issues


# --- Étape 4 : l'assemblage ------------------------------------------------------------
def assemble(
    outline: Outline,
    lessons: dict[tuple[int, int], Lesson],
    subject_id: int,
    source_file: str,
    language: str,
    units: Optional[list[int]] = None,
) -> tuple[ImportDraft, list[Issue]]:
    """Range les leçons dans leurs unités, dans l'ordre du plan. Sans IA.

    `lessons` : {(n° d'unité, n° de leçon): leçon}, numéros comptés à partir de 1.
    Une leçon du plan absente (échec à l'étape 2) est remplacée par une leçon vide,
    signalée LESSON_MISSING : l'admin peut la relancer seule avec /api/parse-lesson/.
    """
    issues: list[Issue] = []
    wanted = set(units) if units else set(range(1, len(outline.units) + 1))
    out_units = []
    for ui, planned_unit in enumerate(outline.units, start=1):
        if ui not in wanted:
            continue
        out_lessons = []
        for li, planned in enumerate(planned_unit.lessons, start=1):
            lesson = lessons.get((ui, li))
            if lesson is None:
                issues.append(Issue(
                    code="LESSON_MISSING", level="error", path=f"units[{len(out_units)}].lessons[{li - 1}]",
                    message=f"La leçon « {planned.title} » (pages {planned.start_page}-{planned.end_page}) "
                            f"n'a pas été détaillée. La relancer seule.",
                ))
                lesson = Lesson(ref=lesson_ref(ui, li), title=planned.title, order_index=li,
                                source_page=planned.start_page, goals=[])
            else:
                # garantit des identifiants uniques et la bonne position, même si FATIN a modifié la leçon
                lesson = rename_refs(lesson, ui, li).model_copy(update={"order_index": li})
            out_lessons.append(lesson)
        first_page = planned_unit.lessons[0].start_page if planned_unit.lessons else None
        out_units.append(Unit(
            ref=f"u{ui}", title=planned_unit.title, central_question=planned_unit.central_question,
            order_index=ui, origin=planned_unit.origin, source_page=first_page, lessons=out_lessons,
        ))
    draft = ImportDraft(subject=subject_id, source_file=source_file, language=language, units=out_units)
    return draft, issues


def run_assemble(
    request: AssembleRequest,
    provider: Optional[AIProvider],
    retry_attempts: int = 4,
    retry_wait_seconds: float = 15,
    progress: Progress = None,
) -> ParseResponse:
    """Étapes 3 + 4. Si l'étape 3 échoue, le brouillon est quand même livré, sans les liens."""
    started = time.monotonic()
    lessons = {(p.unit_index, p.lesson_index): p.lesson for p in request.lessons}
    draft, issues = assemble(
        request.outline, lessons, request.subject, request.source_file, request.language, request.units
    )

    repaired = False
    lesson_count = sum(len(u.lessons) for u in draft.units if u.lessons)
    if request.find_links and provider is not None and lesson_count >= 2:
        if progress:
            progress("Étape 3 : recherche des prérequis entre leçons...")
        try:
            links, repaired = find_links(
                draft, provider, request.stage, request.level, request.subject_name,
                retry_attempts, retry_wait_seconds, progress,
            )
            draft, link_issues = apply_links(draft, links)
            issues += link_issues
        except (ProviderError, PipelineError) as exc:
            issues.append(Issue(
                code="LINKS_FAILED", level="warning", path="prerequisites",
                message=f"Les prérequis entre leçons n'ont pas pu être proposés ({exc}). À ajouter à la main.",
            ))

    if progress:
        progress("Étape 4 : assemblage et vérification de tout le brouillon...")
    # LESSON_MISSING remplace LESSON_GOALS pour une leçon vide (un seul message, plus clair)
    missing = {i.path for i in issues if i.code == "LESSON_MISSING"}
    for issue in check_draft(draft):
        if issue.code == "LESSON_GOALS" and issue.path.removesuffix(".goals") in missing:
            continue
        issues.append(issue)

    return ParseResponse(
        draft=draft,
        issues=issues,
        meta=Meta(
            provider=provider.name if provider else "none",
            model=provider.model if provider else "",
            duration_seconds=round(time.monotonic() - started, 2),
            repaired=repaired,
        ),
    )
