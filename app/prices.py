"""
Prix des modèles d'IA (dollars US), pour calculer le coût RÉEL d'un benchmark ou d'un livre.

Relevés le 20 octobre 2026 sur les pages officielles (Google, OpenAI, Anthropic) et, pour
Mistral, sur un comparateur qui reprend la page officielle. LES PRIX CHANGENT : vérifier
avant de présenter un budget, et mettre à jour ce fichier.

  input  : $ par million de tokens envoyés (consigne + pages)
  output : $ par million de tokens reçus (réponse + raisonnement)
  ocr    : $ pour 1 000 pages (Mistral OCR seulement)
"""
from typing import Optional

from .providers.base import Usage

PRICES_DATE = "2026-10-20"

PRICES: dict[str, dict] = {
    # Google Gemini — https://ai.google.dev/gemini-api/docs/pricing
    "gemini-3.8-flash": {"input": 0.75, "output": 3.75, "note": "1,50 / 7,50 à partir du 1er janvier 2027"},
    "gemini-3.6-flash": {"input": 0.75, "output": 3.75, "note": "1,50 / 7,50 à partir du 1er janvier 2027"},
    "gemini-3.5-flash-lite": {"input": 0.30, "output": 2.50},
    "gemini-3.1-flash-lite": {"input": 0.25, "output": 1.50},
    "gemini-3.1-pro-preview": {"input": 2.00, "output": 12.00},
    # OpenAI GPT — https://developers.openai.com/api/docs/pricing
    "gpt-6-astra": {"input": 10.00, "output": 50.00},
    "gpt-6.1-sol": {"input": 2.00, "output": 10.00},
    "gpt-6-luna": {"input": 0.10, "output": 0.50},
    # Anthropic Claude — https://platform.claude.com/docs/en/about-claude/pricing
    "claude-fable-5-1": {"input": 10.00, "output": 50.00},
    "claude-opus-5-5": {"input": 4.00, "output": 20.00},
    "claude-sonnet-5-5": {"input": 2.00, "output": 10.00},
    "claude-haiku-5-5": {"input": 0.10, "output": 0.50, "note": "0,50 / 2,50 si la requête dépasse 100 000 tokens"},
    # Mistral — https://mistral.ai/pricing (prix repris d'un comparateur, juin 2026)
    "mistral-medium-latest": {"input": 1.50, "output": 7.50, "note": "Mistral Medium 3.5"},
    "mistral-large-latest": {"input": 0.50, "output": 1.50, "note": "Mistral Large 3"},
    "mistral-small-latest": {"input": 0.10, "output": 0.30, "note": "Mistral Small 4"},
    "mistral-ocr-latest": {"ocr": 2.00, "note": "par 1 000 pages"},
}

MISTRAL_OCR = "mistral-ocr-latest"


def price_of(model: str) -> Optional[dict]:
    return PRICES.get(model)


def cost_of(model: str, usage: Usage) -> Optional[float]:
    """Coût en dollars d'une consommation. None si le prix du modèle n'est pas connu."""
    price = price_of(model)
    if price is None or "input" not in price:
        return None
    cost = usage.input_tokens / 1e6 * price["input"] + usage.output_tokens / 1e6 * price["output"]
    if usage.ocr_pages:
        cost += usage.ocr_pages / 1000 * PRICES[MISTRAL_OCR]["ocr"]
    return cost
