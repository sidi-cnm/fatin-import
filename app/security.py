"""Protection du service : seul le serveur FATIN, qui connaît la clé secrète, peut l'appeler."""
import hmac

from fastapi import Header, HTTPException, status

from .config import get_settings


def require_api_key(x_api_key: str = Header(default="", alias="X-API-Key")) -> None:
    expected = get_settings().service_api_key
    if not expected:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="SERVICE_API_KEY n'est pas configurée sur le serveur.",
        )
    if not hmac.compare_digest(x_api_key, expected):
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Clé API invalide.")
