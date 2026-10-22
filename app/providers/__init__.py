from ..config import Settings
from .base import AIProvider, ProviderError, QuotaExhaustedError, SourceDocument, TransientProviderError, Usage

PROVIDERS = ("gemini", "mistral", "openai", "anthropic", "fake")


def get_provider(settings: Settings, subject_id: int, name: str | None = None, model: str | None = None) -> AIProvider:
    """Choisit le fournisseur : celui demandé, sinon celui de AI_PROVIDER.

    `model` remplace le modèle du .env (utile pour le benchmark : plusieurs modèles d'un même fournisseur).
    """
    choice = (name or settings.provider).lower()
    if choice == "gemini":
        from .gemini import GeminiProvider

        return GeminiProvider(
            settings.gemini_api_key,
            model or settings.gemini_model,
            settings.request_timeout_seconds,
            fallback_model="" if model else settings.gemini_fallback_model,
        )
    if choice == "mistral":
        from .mistral import MistralProvider

        return MistralProvider(
            settings.mistral_api_key,
            model or settings.mistral_model,
            settings.mistral_ocr_model,
            settings.request_timeout_seconds,
            mode=settings.mistral_mode,
        )
    if choice == "openai":
        from .gpt import GPTProvider

        return GPTProvider(settings.openai_api_key, model or settings.openai_model, settings.request_timeout_seconds)
    if choice == "anthropic":
        from .claude import ClaudeProvider

        return ClaudeProvider(
            settings.anthropic_api_key, model or settings.anthropic_model, settings.request_timeout_seconds
        )
    if choice == "fake":
        from .fake import FakeProvider

        return FakeProvider(subject_id)
    raise ProviderError(f"Fournisseur inconnu : {choice} ({', '.join(PROVIDERS)}).")


__all__ = [
    "AIProvider", "PROVIDERS", "ProviderError", "QuotaExhaustedError", "SourceDocument", "TransientProviderError",
    "Usage", "get_provider",
]
