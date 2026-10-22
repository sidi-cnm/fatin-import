"""
Fournisseur Google Gemini (SDK `google-genai`).

Avantage clé pour FATIN : Gemini lit le PDF directement, comme une image.
On évite l'extraction de texte, qui casse l'arabe (lettres « dessinées », mots inversés).
"""
from typing import Optional

from .base import AIProvider, ProviderError, SourceDocument, TransientProviderError, wrap_error


class GeminiProvider(AIProvider):
    name = "gemini"

    def __init__(self, api_key: str, model: str, timeout_seconds: int = 180, fallback_model: str = ""):
        if not api_key:
            raise ProviderError("GEMINI_API_KEY manquante.")
        try:
            from google import genai
            from google.genai import types
        except ImportError as exc:  # pragma: no cover
            raise ProviderError("Installer le paquet : pip install google-genai") from exc
        self._types = types
        self._client = genai.Client(
            api_key=api_key, http_options=types.HttpOptions(timeout=timeout_seconds * 1000)
        )
        self.model = model
        self._fallback_model = fallback_model

    def generate(self, doc: SourceDocument, instructions: str, repair: Optional[str] = None) -> str:
        try:
            return self._generate_with(self.model, doc, instructions, repair)
        except TransientProviderError:
            # modèle principal surchargé : on tente tout de suite le modèle de secours, s'il existe
            if not self._fallback_model or self._fallback_model == self.model:
                raise
            text = self._generate_with(self._fallback_model, doc, instructions, repair)
            self.model = self._fallback_model  # pour que `meta` indique le modèle réellement utilisé
            return text

    def _generate_with(self, model: str, doc: SourceDocument, instructions: str, repair: Optional[str]) -> str:
        t = self._types
        parts = [t.Part.from_text(text=instructions)]
        if doc.text is not None:
            parts.append(t.Part.from_text(text=doc.text))
        elif doc.pages:
            # chaque page précédée de son numéro dans le livre : l'IA renvoie des numéros exacts
            for i, page in enumerate(doc.pages):
                parts.append(t.Part.from_text(text=f"=== PAGE {doc.first_page + i} ==="))
                parts.append(t.Part.from_bytes(data=page, mime_type=doc.pages_mime))
        else:
            parts.append(t.Part.from_bytes(data=doc.data, mime_type=doc.mime_type))
        if repair:
            parts.append(t.Part.from_text(text=repair))
        try:
            response = self._client.models.generate_content(
                model=model,
                contents=[t.Content(role="user", parts=parts)],
                config=t.GenerateContentConfig(response_mime_type="application/json", temperature=0.2),
            )
        except Exception as exc:  # quota, réseau, modèle inconnu...
            raise wrap_error("Gemini", exc) from exc
        meta = getattr(response, "usage_metadata", None)
        if meta is not None:  # le raisonnement (« thoughts ») est facturé au prix de sortie
            self.add_usage(
                input_tokens=meta.prompt_token_count or 0,
                output_tokens=(meta.candidates_token_count or 0) + (meta.thoughts_token_count or 0),
            )
        else:
            self.add_usage()
        if not response.text:
            raise ProviderError("Gemini n'a renvoyé aucun texte.")
        return response.text
