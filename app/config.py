"""Réglages lus dans les variables d'environnement (ou le fichier .env)."""
import os
from dataclasses import dataclass
from pathlib import Path


def _load_dotenv(path: Path) -> None:
    """Charge un fichier .env simple (CLE=valeur) sans écraser l'environnement existant."""
    if not path.exists():
        return
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        os.environ.setdefault(key.strip(), value.strip().strip('"').strip("'"))


_load_dotenv(Path(__file__).resolve().parent.parent / ".env")


@dataclass(frozen=True)
class Settings:
    service_api_key: str
    provider: str  # gemini | mistral | openai | anthropic | fake
    gemini_api_key: str
    gemini_model: str
    gemini_fallback_model: str
    mistral_api_key: str
    mistral_model: str
    mistral_ocr_model: str
    max_file_mb: int
    request_timeout_seconds: int
    retry_attempts: int
    retry_wait_seconds: float
    pause_between_calls_seconds: float = 4  # découpage automatique : pause entre 2 leçons (quotas gratuits)
    min_lessons_per_unit: int = 3  # création des unités : taille minimale
    max_lessons_per_unit: int = 10  # création des unités : taille maximale
    mistral_mode: str = "ocr"  # ocr (texte, sans les dessins) | vision (images des pages)
    openai_api_key: str = ""
    openai_model: str = "gpt-6.1-sol"
    anthropic_api_key: str = ""
    anthropic_model: str = "claude-sonnet-5-5"
    cms_save_dir: str = "resultats/cms"  # simulation du CMS : où « enregistrer dans la base »
    cms_simulation: bool = True  # False en production : /cms et /api/cms/save/ sont coupés


def get_settings() -> Settings:
    return Settings(
        service_api_key=os.getenv("SERVICE_API_KEY", ""),
        provider=os.getenv("AI_PROVIDER", "fake").lower(),
        gemini_api_key=os.getenv("GEMINI_API_KEY", ""),
        gemini_model=os.getenv("GEMINI_MODEL", "gemini-flash-latest"),
        gemini_fallback_model=os.getenv("GEMINI_FALLBACK_MODEL", ""),
        mistral_api_key=os.getenv("MISTRAL_API_KEY", ""),
        mistral_model=os.getenv("MISTRAL_MODEL", "mistral-medium-latest"),
        mistral_ocr_model=os.getenv("MISTRAL_OCR_MODEL", "mistral-ocr-latest"),
        max_file_mb=int(os.getenv("MAX_FILE_MB", "60")),
        request_timeout_seconds=int(os.getenv("REQUEST_TIMEOUT_SECONDS", "180")),
        retry_attempts=int(os.getenv("RETRY_ATTEMPTS", "4")),
        retry_wait_seconds=float(os.getenv("RETRY_WAIT_SECONDS", "15")),
        pause_between_calls_seconds=float(os.getenv("PAUSE_BETWEEN_CALLS_SECONDS", "4")),
        min_lessons_per_unit=int(os.getenv("MIN_LESSONS_PER_UNIT", "3")),
        max_lessons_per_unit=int(os.getenv("MAX_LESSONS_PER_UNIT", "10")),
        mistral_mode=os.getenv("MISTRAL_MODE", "ocr").lower(),
        openai_api_key=os.getenv("OPENAI_API_KEY", ""),
        openai_model=os.getenv("OPENAI_MODEL", "gpt-6.1-sol"),
        anthropic_api_key=os.getenv("ANTHROPIC_API_KEY", ""),
        anthropic_model=os.getenv("ANTHROPIC_MODEL", "claude-sonnet-5-5"),
        cms_save_dir=os.getenv("CMS_SAVE_DIR", "resultats/cms"),
        cms_simulation=os.getenv("CMS_SIMULATION", "true").strip().lower() in ("1", "true", "yes", "oui"),
    )
