"""
Règles pédagogiques de FATIN, vérifiées sur le brouillon.

Le format (types, champs) est déjà garanti par schemas.py.
Ici on vérifie le SENS : 2 objectifs par leçon, 1 rule + 1 example + 2 practice, etc.
Le service ne refuse pas un brouillon qui a des erreurs : il les signale dans `issues`,
pour que l'admin les corrige dans l'écran de relecture. FATIN revérifie à l'enregistrement.
"""
import re
from collections import Counter

from .text_cleaning import has_arabic, has_presentation_forms
from .schemas import ImportDraft, Issue

MIN_CHUNKS = {"rule": 1, "example": 1, "practice": 2}


def check_draft(draft: ImportDraft) -> list[Issue]:
    issues: list[Issue] = []

    def err(code: str, path: str, message: str) -> None:
        issues.append(Issue(code=code, level="error", path=path, message=message))

    def warn(code: str, path: str, message: str) -> None:
        issues.append(Issue(code=code, level="warning", path=path, message=message))

    if not draft.units:
        err("NO_UNITS", "units", "Aucune unité trouvée dans le document.")

    all_refs: list[str] = []
    skills_by_ref: dict[str, list[str]] = {}  # ref -> prerequisites

    for ui, unit in enumerate(draft.units):
        up = f"units[{ui}]"
        all_refs.append(unit.ref)
        if not unit.lessons:
            warn("UNIT_NO_LESSONS", up, f"L'unité « {unit.title} » n'a aucune leçon.")
        _check_order([l.order_index for l in unit.lessons], f"{up}.lessons", warn)

        for li, lesson in enumerate(unit.lessons):
            lp = f"{up}.lessons[{li}]"
            all_refs.append(lesson.ref)
            types = Counter(g.goal_type for g in lesson.goals)
            if len(lesson.goals) != 2 or types["conceptual"] != 1 or types["operational"] != 1:
                err(
                    "LESSON_GOALS",
                    f"{lp}.goals",
                    f"La leçon « {lesson.title} » doit avoir exactement 2 objectifs : "
                    f"1 conceptuel et 1 opérationnel (trouvé : {len(lesson.goals)}).",
                )

            for gi, goal in enumerate(lesson.goals):
                gp = f"{lp}.goals[{gi}]"
                if not goal.skills:
                    err("GOAL_NO_SKILLS", f"{gp}.skills", f"L'objectif « {goal.title} » n'a aucune skill.")
                _check_order([s.order_index for s in goal.skills], f"{gp}.skills", warn)

                for si, skill in enumerate(goal.skills):
                    sp = f"{gp}.skills[{si}]"
                    all_refs.append(skill.ref)
                    skills_by_ref[skill.ref] = skill.prerequisites
                    if skill.skill_type != goal.goal_type:
                        err(
                            "SKILL_TYPE",
                            f"{sp}.skill_type",
                            f"La skill « {skill.name} » est {skill.skill_type} "
                            f"mais son objectif est {goal.goal_type}.",
                        )
                    counts = Counter(c.chunk_type for c in skill.chunks)
                    for ctype, minimum in MIN_CHUNKS.items():
                        if counts[ctype] < minimum:
                            err(
                                "SKILL_MIN_CHUNKS",
                                f"{sp}.chunks",
                                f"La skill « {skill.name} » a {counts[ctype]} chunk(s) {ctype}, "
                                f"il en faut au moins {minimum}.",
                            )
                    _check_order([c.order_index for c in skill.chunks], f"{sp}.chunks", warn)

                    for ci, chunk in enumerate(skill.chunks):
                        _check_chunk(chunk, f"{sp}.chunks[{ci}]", err, warn)

    # refs uniques
    for ref, n in Counter(all_refs).items():
        if n > 1:
            err("DUPLICATE_REF", "units", f"La ref « {ref} » est utilisée {n} fois.")

    # prerequisites : existent, pas soi-même, pas de boucle
    for ref, prereqs in skills_by_ref.items():
        for p in prereqs:
            if p == ref:
                err("PREREQ_SELF", ref, f"La skill « {ref} » est son propre prérequis.")
            elif p not in skills_by_ref:
                err("PREREQ_UNKNOWN", ref, f"Le prérequis « {p} » de la skill « {ref} » n'existe pas.")
    cycle = _find_cycle(skills_by_ref)
    if cycle:
        err("PREREQ_CYCLE", "prerequisites", "Boucle dans les prérequis : " + " → ".join(cycle))

    # contenu proposé par l'IA dans la mauvaise langue
    for ui, unit in enumerate(draft.units):
        for li, lesson in enumerate(unit.lessons):
            for gi, goal in enumerate(lesson.goals):
                for si, skill in enumerate(goal.skills):
                    for ci, chunk in enumerate(skill.chunks):
                        if chunk.origin != "proposed":
                            continue
                        texts = [chunk.content] + [o.text for o in chunk.options or []]
                        if not _language_ok(" ".join(texts), draft.language):
                            warn(
                                "LANGUAGE_MISMATCH",
                                f"units[{ui}].lessons[{li}].goals[{gi}].skills[{si}].chunks[{ci}]",
                                f"Ce contenu proposé ne semble pas écrit en "
                                f"{'arabe' if draft.language == 'ar' else 'français'}.",
                            )

    # images : celles à ajouter par l'admin, et les textes qui parlent d'une image non prévue
    for ui, unit in enumerate(draft.units):
        for li, lesson in enumerate(unit.lessons):
            for gi, goal in enumerate(lesson.goals):
                for si, skill in enumerate(goal.skills):
                    for ci, chunk in enumerate(skill.chunks):
                        path = f"units[{ui}].lessons[{li}].goals[{gi}].skills[{si}].chunks[{ci}]"
                        if chunk.needs_image and not chunk.image:
                            where = chunk.image_note or (
                                f"voir page {chunk.source_page}" if chunk.source_page else "voir le livre"
                            )
                            warn("IMAGE_NEEDED", path, f"Image à ajouter : {where}")
                        elif mentions_picture(" ".join([chunk.content] + [o.text for o in chunk.options or []])):
                            warn(
                                "IMAGE_REFERENCE", path,
                                "Le texte parle d'une image, mais aucune image n'est prévue : "
                                "ajouter l'image ou réécrire le texte.",
                            )
                        # la bonne réponse dépend de la lecture d'une image : l'admin la vérifie sur la page
                        kind = answer_from_picture(chunk)
                        if kind:
                            page = f"page {chunk.source_page}" if chunk.source_page else "la page du livre"
                            correct = next((o.text for o in chunk.options or [] if o.is_correct), "?")
                            warn(
                                "ANSWER_FROM_PICTURE", path,
                                f"Bonne réponse à vérifier sur {page} : elle dépend de l'image ({kind}). "
                                f"Réponse marquée juste : « {correct} ».",
                            )

    # texte arabe encore cassé après nettoyage
    for path, text in _walk_strings(draft.model_dump()):
        if has_presentation_forms(text):
            warn("ARABIC_BROKEN", path, "Texte arabe abîmé (copié d'un PDF ?). À vérifier ou retaper.")

    # noms fixes des chunks : uniques dans tout le brouillon
    chunk_refs = _chunk_refs(draft)
    seen: dict[str, str] = {}
    for path, ref in chunk_refs.items():
        if ref in seen:
            err("CHUNK_REF_DUPLICATE", path, f"Le nom « {ref} » est déjà utilisé par un autre chunk ({seen[ref]}).")
        else:
            seen[ref] = path
    return [_with_chunk_ref(i, chunk_refs) for i in issues]


_CHUNK_PATH = re.compile(r"^units\[\d+\]\.lessons\[\d+\]\.goals\[\d+\]\.skills\[\d+\]\.chunks\[\d+\]")


def _chunk_refs(draft: ImportDraft) -> dict[str, str]:
    """Chemin de chaque chunk -> son nom fixe (seulement les chunks qui en ont un)."""
    return {
        f"units[{ui}].lessons[{li}].goals[{gi}].skills[{si}].chunks[{ci}]": chunk.ref
        for ui, unit in enumerate(draft.units)
        for li, lesson in enumerate(unit.lessons)
        for gi, goal in enumerate(lesson.goals)
        for si, skill in enumerate(goal.skills)
        for ci, chunk in enumerate(skill.chunks)
        if chunk.ref
    }


def _with_chunk_ref(issue: Issue, chunk_refs: dict[str, str]) -> Issue:
    """Ajoute le nom du chunk à une remarque qui le vise : le CMS la retrouve même si les positions changent."""
    match = _CHUNK_PATH.match(issue.path)
    if match and match.group(0) in chunk_refs:
        return issue.model_copy(update={"ref": chunk_refs[match.group(0)]})
    return issue


def _check_chunk(chunk, path, err, warn) -> None:
    if chunk.chunk_type != "practice":
        if chunk.options:
            err("OPTIONS_NOT_PRACTICE", f"{path}.options", "Seuls les chunks practice ont des réponses.")
        return

    options = chunk.options or []
    if len(options) < 2:
        err("PRACTICE_OPTIONS", f"{path}.options", "Un exercice doit proposer au moins 2 réponses.")
        return
    ids = [o.id for o in options]
    if len(set(ids)) != len(ids):
        err("OPTION_IDS", f"{path}.options", "Deux réponses ont la même lettre.")
    correct = [o for o in options if o.is_correct]
    if len(correct) != 1:
        err("ONE_CORRECT", f"{path}.options", f"Il faut exactement 1 bonne réponse (trouvé : {len(correct)}).")
    for oi, opt in enumerate(options):
        op = f"{path}.options[{oi}]"
        if opt.is_correct and opt.error_category is not None:
            err("CORRECT_HAS_ERROR", op, "La bonne réponse ne doit pas avoir de type d'erreur.")
        if not opt.is_correct and opt.error_category is None:
            err("WRONG_NO_ERROR", op, f"La mauvaise réponse « {opt.text} » n'a pas de type d'erreur.")
        if not opt.is_correct and not opt.feedback:
            warn("WRONG_NO_FEEDBACK", op, f"La mauvaise réponse « {opt.text} » n'a pas de message pour l'élève.")


# Mots qui renvoient à une image (arabe sans voyelles, français)
_PICTURE_WORDS = {
    "صورة", "الصورة", "صوره", "الصوره", "صور", "الصور", "رسم", "الرسم", "رسوم", "الرسوم", "المرسوم",
    "image", "images", "dessin", "dessins", "illustration", "illustrations", "photo", "photos",
}
_HARAKAT = dict.fromkeys(list(range(0x064B, 0x0653)) + [0x0670, 0x0640])


def mentions_picture(text: str) -> bool:
    """Vrai si le texte renvoie à une image (« في الصورة », « sur le dessin »...)."""
    import re

    for word in re.findall(r"\w+", text.translate(_HARAKAT).lower()):
        if word in _PICTURE_WORDS:
            return True
        # préfixes collés : و / ف / ل (« والصورة », « للصورة »)
        if len(word) > 4 and word[0] in "وفل" and (word[1:] in _PICTURE_WORDS or "ال" + word[2:] in _PICTURE_WORDS):
            return True
    return False


# --- Réponses qui dépendent de la lecture d'une image (ANSWER_FROM_PICTURE) ---------------------
# Dans nos tests, TOUTES les erreurs graves de l'IA venaient de là : compter des objets d'un dessin,
# droite / gauche dans une image, plus grand / plus petit. L'IA ne s'en rend pas compte elle-même :
# on signale donc à l'admin chaque exercice de ce genre, avec sa page, pour qu'il vérifie la réponse.
_COUNT_WORDS = {
    "كم", "عدد", "اعد", "نعد", "يعد", "تعد", "احسب", "نحسب", "يحتوي", "تحتوي", "مجموعه", "مجموعات",
    "combien", "compte", "compter", "comptes", "nombre",
}
_NUMBER_WORDS = ("واحد", "اثن", "ثلاث", "اربع", "خمس", "ست", "سبع", "ثمان", "تسع", "عشر")
_POSITION_WORDS = {
    "يمين", "يسار", "يمني", "يسري", "فوق", "تحت", "امام", "خلف", "جنب", "وراء", "بجانب", "بين",
    "droite", "gauche", "dessus", "dessous", "devant", "derrière", "derriere", "côté", "cote", "entre",
}
_SIZE_WORDS = {
    "اطول", "اقصر", "اكبر", "اصغر", "اثقل", "اخف", "اكثر", "اقل", "اعلي", "اسفل", "اغلي", "ارخص",
    "اقرب", "ابعد", "long", "court", "grand", "petit", "lourd", "léger", "leger", "haut", "bas",
}
_AR_PREFIXES = ("وال", "فال", "بال", "كال", "لل", "ال", "و", "ف", "ب", "ل", "ك")


def _words(text: str) -> list[set[str]]:
    """Mots sans harakat, avec leurs formes sans préfixe (« والكرة » -> {والكره, الكره, كره})."""
    import re

    text = text.translate(_HARAKAT).lower()
    for a, b in (("أ", "ا"), ("إ", "ا"), ("آ", "ا"), ("ة", "ه"), ("ى", "ي")):
        text = text.replace(a, b)
    out = []
    for word in re.findall(r"\w+", text):
        forms = {word}
        for prefix in _AR_PREFIXES:
            if word.startswith(prefix) and len(word) - len(prefix) >= 2:
                forms.add(word[len(prefix):])
        out.append(forms)
    return out


def answer_from_picture(chunk) -> str | None:
    """Pour un exercice dont la bonne réponse se lit sur une image : le genre de lecture
    (« comptage », « position », « taille / comparaison »). Sinon None.

    Il faut à la fois : une image (needs_image, ou un texte qui parle d'une image) ET une
    question qui fait compter, situer ou comparer quelque chose sur cette image.
    """
    if chunk.chunk_type != "practice" or not chunk.options:
        return None
    text = " ".join([chunk.content] + [o.text for o in chunk.options])
    if not (chunk.needs_image or mentions_picture(text)):
        return None
    words = _words(text)
    forms = set().union(*words) if words else set()
    kinds = []
    has_number = any(f.isdigit() or f.startswith(_NUMBER_WORDS) for f in forms)
    if forms & _COUNT_WORDS or has_number:
        kinds.append("comptage")
    if forms & _POSITION_WORDS:
        kinds.append("position")
    if forms & _SIZE_WORDS:
        kinds.append("taille / comparaison")
    return ", ".join(kinds) or None


def _language_ok(text: str, language: str) -> bool:
    """Contrôle simple : un texte arabe doit contenir des lettres arabes, un texte français
    doit contenir surtout des lettres latines (quelques mots arabes restent permis)."""
    letters = [c for c in text if c.isalpha()]
    if not letters:
        return True  # que des chiffres ou symboles : rien à dire
    arabic = sum(1 for c in letters if has_arabic(c))
    if language == "ar":
        return arabic > 0
    return arabic / len(letters) < 0.5


def _check_order(values: list[int], path: str, warn) -> None:
    if len(set(values)) != len(values):
        warn("ORDER_DUPLICATE", path, "Deux éléments ont le même order_index.")


def _find_cycle(graph: dict[str, list[str]]) -> list[str] | None:
    """Renvoie une boucle de prérequis (liste de refs) ou None."""
    WHITE, GREY, BLACK = 0, 1, 2
    color = {node: WHITE for node in graph}
    stack: list[str] = []

    def visit(node: str) -> list[str] | None:
        color[node] = GREY
        stack.append(node)
        for nxt in graph.get(node, []):
            if nxt not in color:
                continue
            if color[nxt] == GREY:
                return stack[stack.index(nxt):] + [nxt]
            if color[nxt] == WHITE:
                found = visit(nxt)
                if found:
                    return found
        stack.pop()
        color[node] = BLACK
        return None

    for node in graph:
        if color[node] == WHITE:
            found = visit(node)
            if found:
                return found
    return None


def _walk_strings(value, path: str = ""):
    if isinstance(value, str):
        yield path, value
    elif isinstance(value, list):
        for i, v in enumerate(value):
            yield from _walk_strings(v, f"{path}[{i}]")
    elif isinstance(value, dict):
        for k, v in value.items():
            yield from _walk_strings(v, f"{path}.{k}" if path else k)
