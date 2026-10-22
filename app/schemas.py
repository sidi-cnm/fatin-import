"""
Le contrat entre ce service et FATIN.

Ces modèles décrivent EXACTEMENT le JSON que le service renvoie.
Les noms de champs reprennent ceux du Swagger de l'équipe FATIN
(title, central_question, goal_type, skill_type, chunk_type, order_index...).

Règle d'or : rien de ce que FATIN crée lui-même n'apparaît ici
(pas d'id, pas de code, pas de created_at, pas de is_published, pas de correct_answer).
"""
from typing import Literal, Optional

from pydantic import BaseModel, ConfigDict, Field
from pydantic.json_schema import SkipJsonSchema

# --- Valeurs autorisées (identiques aux ENUM de la base FATIN) -------------------
GoalType = Literal["conceptual", "operational"]
ChunkType = Literal["rule", "example", "practice", "error", "hint", "real_life"]
ErrorCategory = Literal["concept", "procedure", "careless"]
Origin = Literal["extracted", "proposed"]  # extrait du document / proposé par l'IA
Language = Literal["ar", "fr"]  # langue principale du document


class Strict(BaseModel):
    """Refuse tout champ inconnu : l'IA ne peut pas inventer de nouvelles clés."""

    model_config = ConfigDict(extra="forbid")


class Option(Strict):
    id: str = Field(pattern=r"^[a-z]$", description="Lettre de la réponse : a, b, c...")
    text: str = Field(min_length=1)
    is_correct: bool
    error_category: Optional[ErrorCategory] = Field(
        default=None, description="Obligatoire pour une mauvaise réponse, null pour la bonne"
    )
    feedback: Optional[str] = Field(default=None, description="Message court affiché à l'élève")


class Chunk(Strict):
    # Nom FIXE du chunk, donné par notre service (jamais par l'IA) : u1-l2-s1-c3 = unité 1, leçon 2,
    # compétence 1, chunk 3. Il ne change plus, même si l'admin supprime ou ajoute des chunks.
    # Côté FATIN : champ Chunk.code (30 caractères max).
    ref: SkipJsonSchema[Optional[str]] = Field(
        default=None, max_length=30, pattern=r"^[\w\-]+$",
        description="Nom fixe du chunk (ex. u1-l2-s1-c3), à enregistrer dans Chunk.code",
    )
    chunk_type: ChunkType
    title: Optional[str] = None
    content: str = Field(min_length=1)
    difficulty: int = Field(default=1, ge=1, le=5)
    order_index: int = Field(ge=1)
    origin: Origin
    source_page: Optional[int] = Field(default=None, ge=1)
    options: Optional[list[Option]] = Field(default=None, description="Seulement pour practice")
    needs_image: bool = Field(
        default=False, description="True si le chunk ne se comprend qu'avec une image du livre : l'admin l'ajoute"
    )
    image_note: Optional[str] = Field(
        default=None, description="Quelle image ajouter : page et ce qu'elle montre (ex. « page 8 : une fille assise sur un lit »)"
    )
    # Le LIEN chunk -> image : rempli par le CMS quand l'admin a ajouté l'image, jamais par l'IA.
    # Soit l'adresse de l'image sur le serveur du CMS (https://...), soit un fichier "images/<ref>.png"
    # (simulation, enregistrement en fichiers).
    image: SkipJsonSchema[Optional[str]] = Field(
        default=None, pattern=r"^(images/[\w\-]+\.(png|jpg)|https?://[^\s\"'<>]+)$",
        description="Image ajoutée par l'admin : adresse https://... ou fichier images/<ref>.png",
    )


class Skill(Strict):
    ref: str = Field(min_length=1, description="Identifiant temporaire, ex. s1")
    name: str = Field(min_length=1)
    description: Optional[str] = None
    skill_type: GoalType
    difficulty: int = Field(default=1, ge=1, le=5)
    order_index: int = Field(ge=1)
    prerequisites: list[str] = Field(default_factory=list, description="refs d'autres skills")
    origin: Origin
    chunks: list[Chunk]


class Goal(Strict):
    goal_type: GoalType
    title: str = Field(min_length=1)
    description: Optional[str] = None
    order_index: int = Field(ge=1)
    origin: Origin
    skills: list[Skill]


class Lesson(Strict):
    ref: str = Field(min_length=1)
    title: str = Field(min_length=1)
    description: Optional[str] = None
    order_index: int = Field(ge=1)
    origin: Origin = "extracted"
    source_page: Optional[int] = Field(default=None, ge=1)
    goals: list[Goal]


class Unit(Strict):
    ref: str = Field(min_length=1)
    title: str = Field(min_length=1)
    description: Optional[str] = None
    central_question: Optional[str] = None
    main_goal_description: Optional[str] = None
    main_skill_domain: Optional[str] = None
    order_index: int = Field(ge=1)
    origin: Origin = "extracted"
    source_page: Optional[int] = Field(default=None, ge=1)
    lessons: list[Lesson]


class ImportDraft(Strict):
    """Le brouillon d'import : c'est ce que FATIN affiche puis enregistre."""

    subject: int = Field(description="id de la matière dans FATIN, reçu dans la requête")
    source_file: str
    language: Language = Field(
        description="ar = arabe (affichage de droite à gauche), fr = français (de gauche à droite)"
    )
    units: list[Unit]


# --- Enveloppe de la réponse du service --------------------------------------------
class Issue(BaseModel):
    """Un problème détecté. FATIN l'affiche dans l'écran de relecture."""

    code: str  # stable, pour traduire côté FATIN (ex. LESSON_GOALS)
    level: Literal["error", "warning"]
    path: str  # où se trouve le problème, ex. units[0].lessons[0].goals
    message: str
    ref: Optional[str] = None  # le chunk concerné (son nom fixe), quand la remarque vise un chunk


class Meta(BaseModel):
    provider: str
    model: str
    duration_seconds: float
    repaired: bool = False  # True si l'IA a dû corriger son JSON une seconde fois


class ParseResponse(BaseModel):
    draft: ImportDraft
    issues: list[Issue]
    meta: Meta


class ValidateResponse(BaseModel):
    valid: bool
    issues: list[Issue]


# --- Découpage automatique d'un livre entier ---------------------------------------
# Étape 1 : le plan. Les pages sont des POSITIONS dans le fichier PDF (1 = première page
# du fichier), pas les numéros imprimés sur les pages.
class OutlineLesson(Strict):
    title: str = Field(min_length=1)
    start_page: int = Field(ge=1, description="1re page de la leçon (position dans le fichier)")
    end_page: int = Field(ge=1, description="Dernière page de la leçon (incluse)")


# D'où vient une unité :
#   book  = imprimée dans le livre                         -> origin "extracted"
#   theme = créée par l'IA, leçons consécutives par thème  -> origin "proposed"
#   assessment = ancienne valeur (coupée aux bilans), encore acceptée
UnitBasis = Literal["book", "assessment", "theme"]


class OutlineUnit(Strict):
    title: str = Field(min_length=1)
    central_question: Optional[str] = None
    origin: Origin = "extracted"
    basis: UnitBasis = "book"
    lessons: list[OutlineLesson]


class OutlineBreak(Strict):
    """Pages qui ne sont pas des leçons : bilan, intégration, révision..."""

    title: str = Field(min_length=1)
    start_page: int = Field(ge=1)
    end_page: int = Field(ge=1)


class Outline(Strict):
    """Le plan du livre : unités -> leçons, avec leurs pages. L'admin peut le corriger."""

    units: list[OutlineUnit]
    breaks: list[OutlineBreak] = Field(default_factory=list)


class OutlineResponse(BaseModel):
    outline: Outline
    total_pages: int
    issues: list[Issue]
    meta: Meta


# Étape 2 : une leçon détaillée
class LessonResponse(BaseModel):
    unit_index: int  # 1 = première unité du plan
    lesson_index: int  # 1 = première leçon de l'unité
    lesson: Lesson
    issues: list[Issue]
    meta: Meta


# Étape 3 : les liens entre leçons (réponse de l'IA)
class SkillLink(Strict):
    skill: str
    prerequisites: list[str]


class SkillLinks(Strict):
    links: list[SkillLink]


# Étape 4 : la demande d'assemblage envoyée par FATIN
class LessonPart(BaseModel):
    unit_index: int = Field(ge=1)
    lesson_index: int = Field(ge=1)
    lesson: Lesson


class AssembleRequest(BaseModel):
    subject: int = Field(description="id de la matière dans FATIN")
    source_file: str
    language: Language = "ar"
    outline: Outline = Field(description="Le plan, tel que validé par l'admin")
    lessons: list[LessonPart] = Field(description="Les leçons renvoyées par /api/parse-lesson/")
    units: Optional[list[int]] = Field(
        default=None, description="Unités à garder (1 = la première). Vide = toutes les unités du plan"
    )
    find_links: bool = Field(default=True, description="Demander à l'IA les prérequis entre leçons")
    provider: Optional[str] = None
    stage: str = ""
    level: str = ""
    subject_name: str = ""
