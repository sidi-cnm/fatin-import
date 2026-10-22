"""
Fournisseur OpenAI GPT (SDK `openai`, API Chat Completions).

Comme pour Gemini, chaque page est envoyée comme une image, précédée de « === PAGE n === » :
le modèle voit les dessins et renvoie les numéros de page du livre.
"""
import base64
from typing import Optional

from .base import AIProvider, ProviderError, SourceDocument, wrap_error
from .images import page_images

MAX_OUTPUT_TOKENS = 32000  # réponse + raisonnement : une leçon fait environ 3 000 à 5 000 tokens


class GPTProvider(AIProvider):
    name = "openai"

    def __init__(self, api_key: str, model: str, timeout_seconds: int = 180):
        if not api_key:
            raise ProviderError("OPENAI_API_KEY manquante.")
        try:
            from openai import OpenAI
        except ImportError as exc:  # pragma: no cover
            raise ProviderError("Installer le paquet : pip install openai") from exc
        # max_retries=0 : nos propres nouveaux essais (pipeline.generate_with_retry) suffisent
        self._client = OpenAI(api_key=api_key, timeout=timeout_seconds, max_retries=0)
        self.model = model

    def _user_content(self, doc: SourceDocument, repair: Optional[str]) -> list[dict]:
        parts: list[dict] = []
        if doc.text is not None:
            parts.append({"type": "text", "text": f"DOCUMENT:\n{doc.text}"})
        else:
            parts.append({"type": "text", "text": "DOCUMENT (each page image follows its page number):"})
            for i, image in enumerate(page_images(doc)):
                encoded = base64.b64encode(image).decode("ascii")
                parts.append({"type": "text", "text": f"=== PAGE {doc.first_page + i} ==="})
                parts.append({"type": "image_url",
                              "image_url": {"url": f"data:image/jpeg;base64,{encoded}", "detail": "high"}})
        if repair:
            parts.append({"type": "text", "text": repair})
        parts.append({"type": "text", "text": "Answer with ONE JSON object only."})
        return parts

    def generate(self, doc: SourceDocument, instructions: str, repair: Optional[str] = None) -> str:
        try:
            response = self._client.chat.completions.create(
                model=self.model,
                messages=[
                    {"role": "system", "content": instructions},
                    {"role": "user", "content": self._user_content(doc, repair)},
                ],
                response_format={"type": "json_object"},
                max_completion_tokens=MAX_OUTPUT_TOKENS,
            )
        except Exception as exc:  # quota, réseau, modèle inconnu...
            raise wrap_error("OpenAI", exc) from exc
        usage = getattr(response, "usage", None)  # completion_tokens inclut le raisonnement
        self.add_usage(
            input_tokens=getattr(usage, "prompt_tokens", 0) or 0,
            output_tokens=getattr(usage, "completion_tokens", 0) or 0,
        )
        # réponse vide ou coupée : le JSON sera refusé et l'IA aura sa seconde chance
        return response.choices[0].message.content or ""
