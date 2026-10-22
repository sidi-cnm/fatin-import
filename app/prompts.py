"""
Les consignes données à l'IA.

Écrites en anglais (les modèles suivent mieux les consignes en anglais),
mais le contenu produit reste dans la langue du document (arabe ou français).

La 1re ligne de chaque consigne dit la tâche (TASK: UNIT, OUTLINE, LESSON ou LINKS) :
ça aide le modèle, et le faux fournisseur s'en sert pour savoir quoi répondre.

  - build_instructions          : une unité entière (mode historique, --pages)
  - build_outline_instructions  : étape 1, lire tout le livre et le diviser en unités et leçons
  - build_lesson_instructions   : étape 2, une seule leçon
  - build_links_instructions    : étape 3, les prérequis entre leçons
"""
import json

from .schemas import ImportDraft, Lesson, Outline, SkillLinks

LANGUAGE_NAMES = {"ar": "Arabic (Modern Standard Arabic)", "fr": "French"}

# Règles de contenu communes (une unité ou une leçon)
_CONTENT_RULES = """
CONTENT RULES
1. The document language is given in CONTEXT ("ar" = Arabic, "fr" = French).
   Copy titles and rule texts EXACTLY as written in the document, in correct reading order
   (Arabic right-to-left, French left-to-right), with original numbers, units and symbols (m², ½).
   Write ALL proposed texts (examples, questions, options, feedback, descriptions) in that same
   language. Terms in another language that appear in the document (e.g. a French term in an
   Arabic math book) are kept as they are.
2. Every lesson has EXACTLY 2 goals: one "conceptual" (understand) and one "operational" (do).
3. Every skill is atomic (one observable ability) and its skill_type equals its goal's goal_type.
4. Every skill has at least: 1 "rule", 1 "example", 2 "practice" chunks, in that order.
   Optional chunk types: "error", "hint", "real_life".
5. WHAT EACH CHUNK TYPE IS (respect it strictly):
   - "rule": the KNOWLEDGE the child must learn: a definition, a property or a method, in one or
     two short sentences. Example: "فَوْق: الشَّيْءُ الْمَوْجُودُ أَعْلَى شَيْءٍ آخَر".
     NOT an instruction ("colour the right card", "choose the card..."), NOT a picture caption alone.
   - "example": a SOLVED case that applies the rule, WITH its answer. Example:
     "الْكِتَابُ مَوْضُوعٌ عَلَى الطَّاوِلَةِ ← الْكِتَابُ فَوْقَ الطَّاوِلَةِ". NOT an exercise without its answer.
   - "practice": a question the child answers by choosing ONE option. The question must not contain
     or reveal its answer, and no option may simply repeat the question.
   - "error": a frequent mistake of children and why it is wrong.
   - "hint": a short clue that helps without giving the answer.
   - "real_life": the rule used in the child's daily life.
6. A "practice" chunk has 2 to 4 options with ids a, b, c, d. EXACTLY one has is_correct=true.
   Every wrong option is a mistake a child would REALLY make, and its error_category says WHY:
     - "concept":   the child confuses the notions (e.g. answers "تحت" instead of "فوق")
     - "procedure": the child knows the notion but applies it wrongly (e.g. takes the wrong point of
                    view for right/left, counts one object twice, forgets a step)
     - "careless":  a slip of attention (e.g. answers about another object than the one asked)
   HOW TO WRITE THE WRONG OPTIONS: first solve the exercise yourself. Then, for each wrong option,
   choose the exact step where a child goes wrong (wrong notion, wrong point of view, counts an object
   twice, forgets one...) and write the answer the child would give after that mistake.
   The feedback of a wrong option explains the right idea in ONE short, kind sentence for a child
   (not just "concentrate" or "look again"). The correct option has error_category=null and a short
   praise as feedback. Other chunk types have options=null.
7. PICTURES: many exercises of school books depend on a drawing. When a chunk can only be
   understood with a picture of the book, keep it close to the book, set needs_image=true and
   describe in image_note WHICH picture the admin must add: its page and what it shows
   (e.g. "page 8: a girl sitting on a bed"). The admin will attach the picture.
   Otherwise needs_image=false and image_note=null. Never invent what a picture shows: if you are
   not sure, say so in image_note.
   For a picture exercise, the options are the book's own cards or sentences (e.g. "الْكُرَةُ خَلْفَ
   الطِّفْلِ" / "الْكُرَةُ أَمَامَ الطِّفْلِ"), so that the child must LOOK at the picture to choose.
   NEVER describe the answer inside an option ("the ball that is behind the child") or the question.
   READING A PICTURE (check each point before writing the correct answer):
     - RIGHT / LEFT: decide them as the child sees the page (the child's own right and left hand
       side), NEVER from the Arabic reading order. A person drawn facing the child has his right
       hand on the child's LEFT side. For a row of objects, write in image_note their order from
       the child's left to the child's right.
     - COUNTING: count the objects of each group one by one and write the counts in image_note
       (e.g. "triangles: 2, apples: 3"). Several groups may have the right number.
     - Options only use objects and groups that really are in the picture. Never invent a group
       (e.g. "3 apples") that the admin will not find in the book.
     - The question names ONE thing without doubt: if the picture has two cats, say which one.
     - If you are not sure of what the picture shows, start image_note with "VERIFY:" so that
       the admin checks the correct answer.
8. NEVER write a statement that can be false for some children (e.g. "the right hand is the hand we
   write with"). Spell every word carefully; copy the book's words exactly.
9. origin = "extracted" when the text comes from the document, "proposed" when you wrote it.
   Set source_page for extracted items when you can. When pages are preceded by a marker
   "=== PAGE n ===", use that n.
10. Proposed texts must be short, concrete and suited to children of the given level.
11. order_index starts at 1 inside each parent.
12. NEVER output ids, codes (U01, SK-...), dates, correct_answer, or any field not in the schema.
"""


def _context(**values) -> str:
    language = values.get("language")
    if language:
        values["language_name"] = LANGUAGE_NAMES[language]
    return json.dumps(values, ensure_ascii=False)


def _schema(model) -> str:
    return json.dumps(model.model_json_schema(), ensure_ascii=False)


# --- Une unité entière (mode historique) ----------------------------------------------
def build_instructions(
    subject_id: int, source_file: str, language: str, stage: str, level: str, subject_name: str
) -> str:
    context = _context(
        subject=subject_id, source_file=source_file, language=language,
        stage=stage, level=level, subject_name=subject_name,
    )
    return f"""TASK: UNIT
You convert one chapter of the official Mauritanian school curriculum into the FATIN
skills-based structure. Output ONE JSON object that matches the JSON Schema below. No prose.

HIERARCHY: units -> lessons -> goals -> skills -> chunks -> options.
{_CONTENT_RULES}
13. ref = short temporary ids, unique in the whole output: u1, u1-l1, s1, s2...
    prerequisites = refs of skills that must be mastered first. No self-reference, no cycles.
14. "subject", "source_file" and "language" must be copied from the CONTEXT below.

CONTEXT: {context}

JSON SCHEMA: {_schema(ImportDraft)}
"""


# --- Étape 1 : le plan (repérage) -------------------------------------------------------
def build_outline_instructions(
    language: str,
    total_pages: int,
    pages_labeled: bool,
    bookmarks: list[tuple[str, int]],
    stage: str = "",
    level: str = "",
    subject_name: str = "",
    min_lessons: int = 3,
    max_lessons: int = 10,
) -> str:
    if pages_labeled:
        numbering = (
            'Each page is preceded by a marker "=== PAGE n ===". Use that n, NEVER the page number\n'
            "  printed on the page itself."
        )
    else:
        numbering = (
            "Page numbers are POSITIONS in the PDF file: the first page of the file is 1.\n"
            "  NEVER use the page number printed on the page itself."
        )
    hints = ""
    if bookmarks:
        lines = "\n".join(f"  {title}  -> page {page}" for title, page in bookmarks)
        hints = f"\nBOOKMARKS found in the PDF file (may help; page = position in the file):\n{lines}\n"
    context = _context(
        language=language, total_pages=total_pages, stage=stage, level=level, subject_name=subject_name
    )
    return f"""TASK: OUTLINE
You read a WHOLE Mauritanian school textbook, understand how it is organised, and return its plan:
the units, and inside each unit its lessons with their pages. Do NOT detail the content of the
lessons. Output ONE JSON object matching the schema. No prose.

STEP 1 - READ AND UNDERSTAND THE BOOK
- Read every page. Find every lesson, in book order, and the pages where it starts and ends.
- Find the pages that are NOT lessons: assessment, integration, revision, projects, "math in daily
  life"... (e.g. الحصيلة، تقويم، إدماج، أوظف مكتسباتي، أقوم مكتسباتي، مراجعة، الرياضيات في حياتنا اليومية /
  bilan, évaluation, intégration, révision). List them in "breaks" (title + pages).
  Do NOT list the cover, the foreword or the table of contents.

STEP 2 - DIVIDE THE BOOK INTO UNITS
- If the book PRINTS unit headings (الوحدة، المحور، المجال، الفصل، الباب / unité, chapitre, séquence,
  partie): use them exactly. Each unit: its printed title, origin="extracted", basis="book".
- Otherwise CREATE the units yourself from what the lessons teach: origin="proposed", basis="theme".
  * A unit = CONSECUTIVE lessons that share one theme. Keep the book order, never move or skip a lesson:
    every lesson belongs to exactly one unit.
  * Each unit has {min_lessons} to {max_lessons} lessons; prefer units of 5 to 7 lessons. Cut where the
    theme changes, not where a unit is full.
  * Assessment pages usually close a group of lessons: a unit should not run across them.
  * NEVER separate the parts of one topic: a lesson and its continuation ("تابع", "suite") or its
    numbered parts ("(1)", "(2)") stay in the same unit.
  * Title: short, in the document language ({LANGUAGE_NAMES[language]}), describing ALL the lessons
    of the unit, with correct school vocabulary. NEVER the subject name or the book title.
  * central_question: one question for children that the unit answers.

PAGES
- {numbering}
- start_page / end_page = first / last page (included). A lesson may end on the page where the next
  one starts.
- The table of contents (فهرس / sommaire) helps, but it can be wrong: always check the real pages.
- Copy lesson titles EXACTLY as written in the book.
{hints}
CONTEXT: {context}

JSON SCHEMA: {_schema(Outline)}
"""


# --- Étape 2 : une leçon -------------------------------------------------------------
def build_lesson_instructions(
    language: str,
    unit_title: str,
    unit_lessons: list[str],
    lesson_number: int,
    start_page: int,
    end_page: int,
    pages_labeled: bool,
    stage: str = "",
    level: str = "",
    subject_name: str = "",
) -> str:
    plan = "\n".join(
        f"  {i}. {title}{'   <- THIS LESSON' if i == lesson_number else ''}"
        for i, title in enumerate(unit_lessons, start=1)
    )
    if pages_labeled:
        numbering = f'You receive pages {start_page} to {end_page} of the book, each preceded by "=== PAGE n ===".'
    else:
        numbering = (
            f"You receive pages {start_page} to {end_page} of the book: the first page you see is page "
            f"{start_page} of the book, the next one {start_page + 1}, and so on. Use these numbers for source_page."
        )
    context = _context(
        language=language, unit=unit_title, lesson=unit_lessons[lesson_number - 1],
        pages=f"{start_page}-{end_page}", stage=stage, level=level, subject_name=subject_name,
    )
    return f"""TASK: LESSON
You convert ONE LESSON of the official Mauritanian school curriculum into the FATIN
skills-based structure. Output ONE JSON object for this lesson only, matching the JSON Schema
below. No prose.

HIERARCHY: lesson -> goals -> skills -> chunks -> options.

WHERE THIS LESSON IS
Unit: {unit_title}
Lessons of this unit:
{plan}
{numbering}
The first or the last page may also contain the end of the previous lesson or the start of the
next one: ignore that content, cover ONLY this lesson.
{_CONTENT_RULES}
13. Lesson fields: ref = "l1", title = the lesson title exactly as in the list above, order_index = 1.
14. Skill refs = s1, s2, s3... unique in this lesson. prerequisites = refs of skills OF THIS LESSON
    that must be mastered first. No self-reference, no cycles. Links to skills of other lessons
    are added later: do not invent them.

CONTEXT: {context}

JSON SCHEMA: {_schema(Lesson)}
"""


# --- Étape 3 : les prérequis entre leçons ----------------------------------------------
def build_links_instructions(language: str, stage: str = "", level: str = "", subject_name: str = "") -> str:
    context = _context(language=language, stage=stage, level=level, subject_name=subject_name)
    return f"""TASK: LINKS
You receive the list of the skills of a school textbook, lesson by lesson, in teaching order.
For each skill, give the skills of OTHER lessons that a child must master BEFORE learning it.

RULES
- Give only DIRECT prerequisites (not the prerequisites of the prerequisites).
- Use only the refs of the list. A prerequisite normally comes from an EARLIER lesson.
- Only real, necessary dependencies. When unsure, leave it out. A skill may have none.
- No self-reference, no cycles.
- List only the skills that have at least one prerequisite.

Output ONE JSON object matching the schema, e.g.
{{"links": [{{"skill": "u1-l3-s2", "prerequisites": ["u1-l2-s2"]}}]}}. No prose.

CONTEXT: {context}

JSON SCHEMA: {_schema(SkillLinks)}
"""


# --- Secondes chances ------------------------------------------------------------------
def build_repair_message(previous_output: str, errors: str) -> str:
    return (
        "Your previous JSON did not match the schema. Fix it and output the corrected JSON only.\n"
        f"VALIDATION ERRORS:\n{errors}\n\nPREVIOUS JSON:\n{previous_output}"
    )


def build_rules_repair_message(previous_output: str, problems: list[str]) -> str:
    listed = "\n".join(f"- {p}" for p in problems)
    return (
        "Your JSON matches the schema but breaks these FATIN rules. Fix ALL of them, keep "
        "everything else, and output the corrected JSON only (same schema).\n"
        f"RULE ERRORS:\n{listed}\n\nPREVIOUS JSON:\n{previous_output}"
    )
