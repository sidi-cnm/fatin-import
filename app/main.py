"""
Service d'import FATIN : transforme un document du programme scolaire
en brouillon structuré (unités → leçons → objectifs → skills → chunks → réponses).

Deux façons de l'utiliser :
  - une unité à la fois :         POST /api/parse-document/
  - un livre entier, en 3 temps : POST /api/outline/  →  POST /api/parse-lesson/ (×N)  →  POST /api/assemble/

Lancer : uvicorn app.main:app --reload
Simulation de l'écran de l'admin du CMS : http://localhost:8000/cms
Documentation interactive : http://localhost:8000/docs
"""
from contextlib import contextmanager
from typing import Literal, Optional

from fastapi import Body, Depends, FastAPI, File, Form, HTTPException, UploadFile, status
from pydantic import ValidationError

from .assemble import run_assemble
from .cms_sim import router as cms_router
from .config import get_settings
from .lesson import run_lesson
from .outline import run_outline
from .pipeline import (
    PipelineError,
    UnsupportedFile,
    extract_pdf_pages,
    read_document,
    run_pipeline,
    validate_draft_data,
)
from .providers import ProviderError, QuotaExhaustedError, get_provider
from .schemas import (
    AssembleRequest,
    LessonResponse,
    Outline,
    OutlineResponse,
    ParseResponse,
    ValidateResponse,
)
from .security import require_api_key

app = FastAPI(
    title="FATIN – Service d'import de documents",
    version="0.2.0",
    description="Découpe un document du programme au format d'import FATIN. "
    "Ne stocke rien : FATIN vérifie et enregistre.",
)
app.include_router(cms_router)  # simulation de l'écran de l'admin : http://localhost:8000/cms


@contextmanager
def _http_errors():
    """Transforme les erreurs du service en réponses HTTP claires."""
    try:
        yield
    except UnsupportedFile as exc:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, str(exc)) from exc
    except QuotaExhaustedError as exc:  # FATIN : ne pas relancer tout de suite, réessayer plus tard
        raise HTTPException(
            status.HTTP_429_TOO_MANY_REQUESTS,
            {"message": "Quota de l'IA épuisé : réessayer plus tard (demain pour un quota du jour).",
             "detail": str(exc)[:500]},
        ) from exc
    except ProviderError as exc:
        raise HTTPException(status.HTTP_502_BAD_GATEWAY, str(exc)) from exc
    except PipelineError as exc:
        raise HTTPException(
            422,
            {"message": "L'IA n'a pas produit un JSON valide après 2 essais.", "errors": exc.errors},
        ) from exc


def _read_upload(file: UploadFile, pdf_only: bool = False) -> bytes:
    settings = get_settings()
    data = file.file.read(settings.max_file_mb * 1024 * 1024 + 1)
    if len(data) > settings.max_file_mb * 1024 * 1024:
        raise HTTPException(status.HTTP_413_REQUEST_ENTITY_TOO_LARGE, f"Fichier > {settings.max_file_mb} Mo.")
    if not data:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Fichier vide.")
    if pdf_only and not (file.filename or "").lower().endswith(".pdf"):
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Le découpage automatique accepte seulement un PDF.")
    return data


@app.get("/health", tags=["service"])
def health() -> dict:
    """Vérifie que le service tourne et indique le fournisseur par défaut."""
    return {"status": "ok", "provider": get_settings().provider}


# --- Une unité à la fois ----------------------------------------------------------------
@app.post(
    "/api/parse-document/",
    response_model=ParseResponse,
    dependencies=[Depends(require_api_key)],
    tags=["une unité"],
)
def parse_document(
    file: UploadFile = File(..., description="Le document : .pdf, .docx ou .txt (une unité par fichier)"),
    subject: int = Form(..., description="id de la matière dans FATIN"),
    language: Literal["ar", "fr"] = Form("ar", description="Langue principale du document : ar ou fr"),
    stage: str = Form("", description="ex. الابتدائية"),
    level: str = Form("", description="ex. السنة الأولى"),
    subject_name: str = Form("", description="ex. الرياضيات"),
    provider: Optional[str] = Form(None, description="gemini, mistral, openai, anthropic ou fake (sinon la valeur par défaut)"),
    pages: str = Form("", description="PDF : pages à garder, ex. 5-24 ou 5-10,12 (vide = tout)"),
) -> ParseResponse:
    settings = get_settings()
    data = _read_upload(file)
    with _http_errors():
        filename = file.filename or "document"
        if pages and filename.lower().endswith(".pdf"):
            data = extract_pdf_pages(data, pages)
        doc = read_document(filename, data)
        ai = get_provider(settings, subject_id=subject, name=provider)
        return run_pipeline(
            doc, ai, subject, language, stage, level, subject_name,
            retry_attempts=settings.retry_attempts, retry_wait_seconds=settings.retry_wait_seconds,
        )


# --- Un livre entier : étape 1, le plan ---------------------------------------------------
@app.post(
    "/api/outline/",
    response_model=OutlineResponse,
    dependencies=[Depends(require_api_key)],
    tags=["livre entier"],
)
def outline(
    file: UploadFile = File(..., description="Le livre entier (PDF)"),
    language: Literal["ar", "fr"] = Form("ar"),
    stage: str = Form(""),
    level: str = Form(""),
    subject_name: str = Form(""),
    provider: Optional[str] = Form(None),
) -> OutlineResponse:
    """ÉTAPE 1. Renvoie le plan (unités, leçons, pages de bilan) et les problèmes du plan.
    Les unités viennent du livre (origin=extracted) ou sont créées par les règles 2 et 3
    (origin=proposed, basis=assessment|theme). L'admin vérifie et corrige le plan avant l'étape 2.
    Pages = positions dans le fichier."""
    settings = get_settings()
    data = _read_upload(file, pdf_only=True)
    with _http_errors():
        ai = get_provider(settings, subject_id=0, name=provider)
        return run_outline(
            data, file.filename or "livre.pdf", ai, language, stage, level, subject_name,
            retry_attempts=settings.retry_attempts, retry_wait_seconds=settings.retry_wait_seconds,
            min_lessons=settings.min_lessons_per_unit, max_lessons=settings.max_lessons_per_unit,
        )


# --- Un livre entier : étape 2, une leçon -------------------------------------------------
@app.post(
    "/api/parse-lesson/",
    response_model=LessonResponse,
    dependencies=[Depends(require_api_key)],
    tags=["livre entier"],
)
def parse_lesson(
    file: UploadFile = File(..., description="Le livre entier (PDF), le même qu'à l'étape 1"),
    outline: str = Form(..., description="Le plan validé par l'admin (JSON : {\"units\": [...]})"),
    unit_index: int = Form(..., ge=1, description="1 = première unité du plan"),
    lesson_index: int = Form(..., ge=1, description="1 = première leçon de l'unité"),
    language: Literal["ar", "fr"] = Form("ar"),
    stage: str = Form(""),
    level: str = Form(""),
    subject_name: str = Form(""),
    provider: Optional[str] = Form(None),
) -> LessonResponse:
    """ÉTAPE 2. Détaille UNE leçon du plan. FATIN l'appelle une fois par leçon."""
    settings = get_settings()
    data = _read_upload(file, pdf_only=True)
    try:
        plan = Outline.model_validate_json(outline)
    except ValidationError as exc:
        raise HTTPException(
            422, {"message": "Plan invalide.", "errors": exc.errors()}
        ) from exc
    with _http_errors():
        ai = get_provider(settings, subject_id=0, name=provider)
        return run_lesson(
            data, file.filename or "livre.pdf", plan, unit_index, lesson_index, ai,
            language, stage, level, subject_name,
            retry_attempts=settings.retry_attempts, retry_wait_seconds=settings.retry_wait_seconds,
        )


# --- Un livre entier : étapes 3 et 4, liens + assemblage ----------------------------------
@app.post(
    "/api/assemble/",
    response_model=ParseResponse,
    dependencies=[Depends(require_api_key)],
    tags=["livre entier"],
)
def assemble(request: AssembleRequest = Body(...)) -> ParseResponse:
    """ÉTAPES 3 et 4. Ajoute les prérequis entre leçons (IA), assemble tout et vérifie.
    Renvoie un seul brouillon, au même format que /api/parse-document/."""
    settings = get_settings()
    with _http_errors():
        ai = get_provider(settings, subject_id=request.subject, name=request.provider) if request.find_links else None
        return run_assemble(
            request, ai, retry_attempts=settings.retry_attempts, retry_wait_seconds=settings.retry_wait_seconds
        )


# --- Vérification sans IA ----------------------------------------------------------------
@app.post(
    "/api/validate/",
    response_model=ValidateResponse,
    dependencies=[Depends(require_api_key)],
    tags=["une unité"],
)
def validate(draft: dict = Body(..., description="Un brouillon au format d'import")) -> ValidateResponse:
    """Vérifie un brouillon (format + règles pédagogiques) sans appeler l'IA."""
    return validate_draft_data(draft)
