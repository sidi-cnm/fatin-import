"""
Fournisseur Mistral (SDK `mistralai`), deux façons de lire les pages (MISTRAL_MODE) :

  - "ocr" (par défaut, comme avant) : 1. Mistral OCR transforme le PDF en texte (markdown),
    page par page ; 2. un modèle Mistral découpe ce texte. Les DESSINS sont perdus.
  - "vision" : chaque page est envoyée comme une image, précédée de « === PAGE n === »,
    comme pour Gemini. Le modèle VOIT les dessins (Mistral Medium lit les images).
    C'est le mode à utiliser pour comparer Mistral aux autres modèles de façon juste.

Vérifié avec le SDK mistralai 3.2 (`client.ocr.process`, `client.chat.complete`).
"""
import base64
from typing import Optional

from .base import AIProvider, ProviderError, SourceDocument, wrap_error
from .images import page_images


class MistralProvider(AIProvider):
    name = "mistral"

    def __init__(self, api_key: str, model: str, ocr_model: str, timeout_seconds: int = 180, mode: str = "ocr"):
        if not api_key:
            raise ProviderError("MISTRAL_API_KEY manquante.")
        if mode not in ("ocr", "vision"):
            raise ProviderError(f"MISTRAL_MODE inconnu : {mode} (ocr ou vision).")
        try:
            try:
                from mistralai.client import Mistral  # SDK v3
            except ImportError:
                from mistralai import Mistral  # SDK v1 / v2
        except ImportError as exc:  # pragma: no cover
            raise ProviderError("Installer le paquet : pip install mistralai") from exc
        self._client = Mistral(api_key=api_key, timeout_ms=timeout_seconds * 1000)
        self.model = model
        self._ocr_model = ocr_model
        self.mode = mode

    def _ocr(self, doc: SourceDocument) -> str:
        encoded = base64.b64encode(doc.data).decode("ascii")
        try:
            result = self._client.ocr.process(
                model=self._ocr_model,
                document={"type": "document_url", "document_url": f"data:{doc.mime_type};base64,{encoded}"},
            )
        except Exception as exc:
            raise wrap_error("Mistral OCR", exc) from exc
        info = getattr(result, "usage_info", None)
        self.add_usage(ocr_pages=getattr(info, "pages_processed", None) or len(result.pages), call=False)
        pages = []
        for i, page in enumerate(result.pages):
            text = page.markdown or ""
            if doc.excerpt_chars:
                text = text[: doc.excerpt_chars]  # pour le plan, le haut de chaque page suffit (titres)
            # numéro de la page dans le livre (et pas dans le morceau envoyé)
            pages.append(f"=== PAGE {doc.first_page + i} ===\n{text}")
        return "\n\n".join(pages)

    def _user_content(self, doc: SourceDocument, repair: Optional[str]):
        if doc.text is not None or self.mode == "ocr":
            document_text = doc.text if doc.text is not None else self._ocr(doc)
            user = f"DOCUMENT:\n{document_text}"
            return user + (f"\n\n{repair}" if repair else "")
        parts = [{"type": "text", "text": "DOCUMENT (each page image follows its page number):"}]
        for i, image in enumerate(page_images(doc)):
            encoded = base64.b64encode(image).decode("ascii")
            parts.append({"type": "text", "text": f"=== PAGE {doc.first_page + i} ==="})
            parts.append({"type": "image_url", "image_url": f"data:image/jpeg;base64,{encoded}"})
        if repair:
            parts.append({"type": "text", "text": repair})
        return parts

    def generate(self, doc: SourceDocument, instructions: str, repair: Optional[str] = None) -> str:
        user = self._user_content(doc, repair)
        try:
            response = self._client.chat.complete(
                model=self.model,
                messages=[{"role": "system", "content": instructions}, {"role": "user", "content": user}],
                response_format={"type": "json_object"},
                temperature=0.2,
            )
        except Exception as exc:
            raise wrap_error("Mistral", exc) from exc
        usage = getattr(response, "usage", None)
        self.add_usage(
            input_tokens=getattr(usage, "prompt_tokens", 0) or 0,
            output_tokens=getattr(usage, "completion_tokens", 0) or 0,
        )
        return response.choices[0].message.content
