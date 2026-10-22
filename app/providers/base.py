"""Interface commune à tous les fournisseurs d'IA. Changer de fournisseur = changer de classe."""
import re
from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Optional


@dataclass
class SourceDocument:
    filename: str
    mime_type: str
    data: bytes
    text: Optional[str] = None  # rempli pour Word/texte ; None pour un PDF envoyé tel quel
    # Découpage automatique d'un livre : numéros de pages du livre
    pages: Optional[list[bytes]] = None  # les mêmes pages, une par fichier (Gemini les étiquette une à une)
    pages_mime: str = "application/pdf"  # format de `pages` : PDF d'une page, ou image JPEG (livre lourd)
    first_page: int = 1  # numéro, dans le livre, de la 1re page de `data` / `pages`
    excerpt_chars: Optional[int] = None  # Mistral : ne garder que le début de chaque page (pour le plan)


@dataclass
class Usage:
    """Ce que les appels ont consommé : sert à calculer le coût RÉEL (benchmark, estimation)."""

    calls: int = 0
    input_tokens: int = 0  # consigne + pages (texte et images)
    output_tokens: int = 0  # réponse + raisonnement (« thinking »), facturés au prix de sortie
    ocr_pages: int = 0  # Mistral OCR : facturé à la page


class AIProvider(ABC):
    name: str = "base"
    model: str = ""

    @abstractmethod
    def generate(self, doc: SourceDocument, instructions: str, repair: Optional[str] = None) -> str:
        """Renvoie le JSON brut (texte) produit par l'IA.

        `repair` contient, au second essai, le JSON précédent et les erreurs à corriger.
        """

    @property
    def usage(self) -> Usage:
        if not hasattr(self, "_usage"):
            self._usage = Usage()
        return self._usage

    def reset_usage(self) -> None:
        self._usage = Usage()

    def add_usage(self, input_tokens: int = 0, output_tokens: int = 0, ocr_pages: int = 0, call: bool = True) -> None:
        u = self.usage
        u.calls += 1 if call else 0
        u.input_tokens += int(input_tokens or 0)
        u.output_tokens += int(output_tokens or 0)
        u.ocr_pages += int(ocr_pages or 0)


class ProviderError(RuntimeError):
    """Erreur côté fournisseur (clé absente, modèle inconnu, refus...)."""


class TransientProviderError(ProviderError):
    """Erreur passagère (serveur surchargé, quota par minute, coupure) : ça vaut la peine de réessayer.

    quota       : True si c'est une limite de quota (429 / RESOURCE_EXHAUSTED), pas une surcharge.
    retry_after : secondes d'attente conseillées par le fournisseur (ex. « retryDelay: 37s »), sinon None.
    """

    def __init__(self, message: str, quota: bool = False, retry_after: Optional[float] = None):
        super().__init__(message)
        self.quota = quota
        self.retry_after = retry_after


class QuotaExhaustedError(ProviderError):
    """Quota épuisé pour longtemps (quota du jour, ou 429 qui ne passe pas après tous les essais).

    Inutile de réessayer ni de passer à la leçon suivante : il faut attendre (demain) ou
    activer la facturation. Le travail déjà fait est gardé ; relancer la même commande reprend.
    """


_TRANSIENT_CODES = {408, 429, 500, 502, 503, 504}
_TRANSIENT_WORDS = ("UNAVAILABLE", "RESOURCE_EXHAUSTED", "overloaded", "high demand", "timed out", "timeout")
_QUOTA_WORDS = ("RESOURCE_EXHAUSTED", "exceeded your current quota", "quota")
_DAILY_WORDS = ("PerDay", "per day", "daily", "insufficient_quota")
# crédit prépayé épuisé (Claude, OpenAI) : comme un quota du jour, attendre ne sert à rien
_NO_CREDIT_WORDS = ("credit balance is too low", "insufficient_quota", "billing_hard_limit")


def wrap_error(prefix: str, exc: Exception) -> ProviderError:
    """Transforme une erreur de SDK en ProviderError : passagère, quota du jour, ou définitive."""
    code = getattr(exc, "code", None) or getattr(exc, "status_code", None)
    text = str(exc)
    low = text.lower()
    quota = code == 429 or any(w.lower() in low for w in _QUOTA_WORDS)
    if any(w.lower() in low for w in _NO_CREDIT_WORDS):
        return QuotaExhaustedError(f"{prefix} : crédit épuisé (recharger le compte). {text}")
    if quota and any(w.lower() in low for w in _DAILY_WORDS):
        return QuotaExhaustedError(f"{prefix} : quota du jour épuisé. {text}")
    transient = (isinstance(code, int) and code in _TRANSIENT_CODES) or any(w.lower() in low for w in _TRANSIENT_WORDS)
    if not transient:
        return ProviderError(f"{prefix} : {text}")
    delay = re.search(r"retryDelay['\"]?\s*[:=]\s*['\"]?(\d+(?:\.\d+)?)s", text)
    return TransientProviderError(f"{prefix} : {text}", quota=quota,
                                  retry_after=float(delay.group(1)) if delay else None)
