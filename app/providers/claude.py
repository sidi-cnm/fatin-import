"""
Fournisseur Anthropic Claude (SDK `anthropic`, API Messages).

Claude lit les PDF : chaque page est envoyée comme un PDF d'une page (Claude en reçoit le texte
ET l'image), précédée de « === PAGE n === », comme pour Gemini.
"""
import base64
from typing import Optional

from .base import AIProvider, ProviderError, SourceDocument, wrap_error
from .images import page_pdfs

MAX_OUTPUT_TOKENS = 16000  # une leçon fait environ 3 000 à 5 000 tokens


class ClaudeProvider(AIProvider):
    name = "anthropic"

    def __init__(self, api_key: str, model: str, timeout_seconds: int = 180):
        if not api_key:
            raise ProviderError("ANTHROPIC_API_KEY manquante.")
        try:
            from anthropic import Anthropic
        except ImportError as exc:  # pragma: no cover
            raise ProviderError("Installer le paquet : pip install anthropic") from exc
        # max_retries=0 : nos propres nouveaux essais (pipeline.generate_with_retry) suffisent
        self._client = Anthropic(api_key=api_key, timeout=timeout_seconds, max_retries=0)
        self.model = model

    def _user_content(self, doc: SourceDocument, repair: Optional[str]) -> list[dict]:
        parts: list[dict] = []
        if doc.text is not None:
            parts.append({"type": "text", "text": f"DOCUMENT:\n{doc.text}"})
        else:
            for i, page in enumerate(page_pdfs(doc)):
                parts.append({"type": "text", "text": f"=== PAGE {doc.first_page + i} ==="})
                parts.append({"type": "document", "source": {
                    "type": "base64", "media_type": "application/pdf",
                    "data": base64.standard_b64encode(page).decode("ascii"),
                }})
        if repair:
            parts.append({"type": "text", "text": repair})
        parts.append({"type": "text", "text": "Answer with ONE JSON object only, with no text before or after it."})
        return parts

    def generate(self, doc: SourceDocument, instructions: str, repair: Optional[str] = None) -> str:
        try:
            response = self._client.messages.create(
                model=self.model,
                max_tokens=MAX_OUTPUT_TOKENS,
                system=instructions,
                messages=[{"role": "user", "content": self._user_content(doc, repair)}],
            )
        except Exception as exc:  # quota, réseau, modèle inconnu...
            raise wrap_error("Claude", exc) from exc
        usage = getattr(response, "usage", None)
        self.add_usage(
            input_tokens=(getattr(usage, "input_tokens", 0) or 0)
            + (getattr(usage, "cache_read_input_tokens", 0) or 0)
            + (getattr(usage, "cache_creation_input_tokens", 0) or 0),
            output_tokens=getattr(usage, "output_tokens", 0) or 0,
        )
        return "".join(block.text for block in response.content if getattr(block, "type", "") == "text")
