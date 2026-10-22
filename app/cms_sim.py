"""
Simulation de l'écran d'administration du CMS FATIN (pour tester l'expérience de l'admin).

    uvicorn app.main:app --reload      puis ouvrir   http://localhost:8000/cms

Le parcours est celui du vrai CMS :
  1. l'admin envoie le livre → l'IA renvoie le PLAN (/api/outline/)
  2. l'admin vérifie et corrige le plan (titres, pages) → /api/cms/check-outline/
  3. il lance UNE unité : chaque leçon est détaillée (/api/parse-lesson/), puis l'unité est
     assemblée et vérifiée (/api/assemble/)
  4. il relit chaque leçon à côté de la page du livre, corrige, découpe les images, coche
     les réponses vérifiées, refait la vérification (/api/validate/)
  5. il ENREGISTRE l'unité → /api/cms/save/. Comme il n'y a pas de base de données ici, on
     écrit des fichiers dans CMS_SAVE_DIR (par défaut resultats/cms/) :
        <livre>/unite_<n>/brouillon_ia.json     ce que l'IA a proposé
                          version_admin.json    ce que l'admin a validé (format d'import FATIN)
                          images/*.png          les images ajoutées par l'admin
                          images.json           quelle image va avec quel chunk (pour les développeurs)
                          statistiques.json     le travail de l'admin (gardés, modifiés, supprimés...)
                          plan.json             le plan validé
"""
import base64
import json
import re
from datetime import datetime
from pathlib import Path
from typing import Optional

from fastapi import APIRouter, Body, Depends, HTTPException
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field

from .config import get_settings
from .outline import check_outline
from .pipeline import validate_draft_data
from .schemas import Issue, Outline, ValidateResponse
from .security import require_api_key

STATIC = Path(__file__).resolve().parent / "static"
router = APIRouter()


def require_simulation() -> None:
    """La page de test et l'enregistrement en fichiers n'existent pas en production (CMS_SIMULATION=false)."""
    if not get_settings().cms_simulation:
        raise HTTPException(404, "Not Found")


@router.get("/cms", include_in_schema=False, dependencies=[Depends(require_simulation)])
def cms_page() -> FileResponse:
    """La page de l'admin (une seule page HTML, en arabe)."""
    return FileResponse(STATIC / "cms.html", media_type="text/html; charset=utf-8")


class CheckOutlineRequest(BaseModel):
    outline: Outline
    total_pages: int = Field(ge=1)


@router.post("/api/cms/check-outline/", response_model=list[Issue], dependencies=[Depends(require_api_key)],
             tags=["simulation CMS"])
def cms_check_outline(request: CheckOutlineRequest = Body(...)) -> list[Issue]:
    """Revérifie le plan après les corrections de l'admin (pages, chevauchements, oublis)."""
    s = get_settings()
    return check_outline(request.outline, request.total_pages, s.max_lessons_per_unit, s.min_lessons_per_unit)


class SaveRequest(BaseModel):
    book: str = Field(min_length=1, description="Nom du fichier du livre")
    unit_index: int = Field(ge=1)
    plan: Optional[dict] = None
    draft_ai: dict = Field(description="Le brouillon de l'unité tel que l'IA l'a renvoyé")
    final: dict = Field(description="Le brouillon corrigé par l'admin (format d'import FATIN)")
    images: dict[str, str] = Field(default_factory=dict, description="nom de fichier -> image en data URL")
    stats: dict = Field(default_factory=dict, description="Le travail de l'admin, calculé par la page")


class SaveResponse(BaseModel):
    folder: str
    files: list[str]
    validation: ValidateResponse


def _chunks_with_image(final: dict) -> list[dict]:
    """Les chunks du brouillon final qui ont une image liée (champ « image »)."""
    found = []
    for ui, unit in enumerate(final.get("units") or []):
        for li, lesson in enumerate(unit.get("lessons") or []):
            for gi, goal in enumerate(lesson.get("goals") or []):
                for si, skill in enumerate(goal.get("skills") or []):
                    for ci, chunk in enumerate(skill.get("chunks") or []):
                        if chunk.get("image"):
                            found.append({
                                "image": chunk["image"], "ref": chunk.get("ref"),
                                "chunk": f"units[{ui}].lessons[{li}].goals[{gi}].skills[{si}].chunks[{ci}]",
                                "lesson": lesson.get("title"), "skill": skill.get("ref"),
                                "chunk_type": chunk.get("chunk_type"), "order_index": chunk.get("order_index"),
                                "source_page": chunk.get("source_page"), "image_note": chunk.get("image_note"),
                                "content": (chunk.get("content") or "")[:120],
                            })
    return found


def _safe_name(text: str) -> str:
    """Un nom de dossier sans risque (lettres, chiffres, - et _), arabe compris."""
    stem = Path(text).stem
    return re.sub(r"[^\w\-]+", "_", stem, flags=re.UNICODE).strip("_") or "livre"


@router.post("/api/cms/save/", response_model=SaveResponse,
             dependencies=[Depends(require_simulation), Depends(require_api_key)], tags=["simulation CMS"])
def cms_save(request: SaveRequest = Body(...)) -> SaveResponse:
    """« Enregistrer dans la base » : ici, des fichiers dans CMS_SAVE_DIR."""
    images = {}  # on vérifie toutes les images AVANT d'écrire quoi que ce soit
    for name, data_url in request.images.items():
        match = re.fullmatch(r"data:image/(png|jpeg);base64,([A-Za-z0-9+/=\s]+)", data_url)
        safe = re.sub(r"[^\w\-]+", "_", Path(name).stem).strip("_")
        if not match or not safe:
            raise HTTPException(400, f"Image illisible : {name}")
        images[f"{safe}.{'jpg' if match.group(1) == 'jpeg' else 'png'}"] = base64.b64decode(match.group(2))

    # chaque lien du brouillon doit avoir son fichier, et chaque fichier doit servir à un chunk
    linked = _chunks_with_image(request.final)
    wanted = {c["image"].removeprefix("images/") for c in linked if c["image"].startswith("images/")}
    missing, unused = sorted(wanted - set(images)), sorted(set(images) - wanted)
    if missing or unused:
        raise HTTPException(400, {"message": "Images et chunks ne correspondent pas.", "missing": missing, "unused": unused})

    validation = validate_draft_data(request.final)
    folder = Path(get_settings().cms_save_dir) / _safe_name(request.book) / f"unite_{request.unit_index}"
    (folder / "images").mkdir(parents=True, exist_ok=True)

    def dump(name: str, value) -> str:
        (folder / name).write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding="utf-8")
        return name

    files = [dump("brouillon_ia.json", request.draft_ai), dump("version_admin.json", request.final)]
    if request.plan:
        files.append(dump("plan.json", request.plan))
    for old in (folder / "images").iterdir():  # un nouvel enregistrement remplace les images
        old.unlink()
    for filename, data in images.items():
        (folder / "images" / filename).write_bytes(data)
        files.append(f"images/{filename}")
    files.append(dump("images.json", linked))
    stats = dict(request.stats, saved_at=datetime.now().isoformat(timespec="seconds"),
                 valid=validation.valid, errors=sum(1 for i in validation.issues if i.level == "error"))
    files.append(dump("statistiques.json", stats))
    return SaveResponse(folder=str(folder), files=files, validation=validation)
