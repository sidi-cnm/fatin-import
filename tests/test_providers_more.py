"""GPT, Claude, Mistral « vision » : ce qui est envoyé, et les tokens comptés (sans appeler d'IA)."""
import io
from types import SimpleNamespace

import pytest
from pypdf import PdfWriter

from app.config import get_settings
from app.pipeline import parse_json
from app.prices import cost_of
from app.providers import PROVIDERS, ProviderError, Usage, get_provider
from app.providers.base import QuotaExhaustedError, SourceDocument, wrap_error


def pdf(pages: int) -> bytes:
    writer = PdfWriter()
    for _ in range(pages):
        writer.add_blank_page(width=200, height=280)
    out = io.BytesIO()
    writer.write(out)
    return out.getvalue()


DOC = SourceDocument(filename="l.pdf", mime_type="application/pdf", data=pdf(2), first_page=11)


class Recorder:
    """Faux client : garde la requête et renvoie une réponse au format du SDK."""

    def __init__(self, response):
        self.kwargs, self._response = None, response

    def create(self, **kwargs):
        self.kwargs = kwargs
        return self._response


def test_gpt_sends_labeled_page_images_and_counts_tokens():
    from app.providers.gpt import GPTProvider

    ai = GPTProvider("k", "gpt-6.1-sol")
    rec = Recorder(SimpleNamespace(
        choices=[SimpleNamespace(message=SimpleNamespace(content='{"a": 1}'))],
        usage=SimpleNamespace(prompt_tokens=4000, completion_tokens=6000),
    ))
    ai._client = SimpleNamespace(chat=SimpleNamespace(completions=rec))
    assert ai.generate(DOC, "TASK: LESSON") == '{"a": 1}'
    content = rec.kwargs["messages"][1]["content"]
    labels = [p["text"] for p in content if p["type"] == "text" and p["text"].startswith("===")]
    assert labels == ["=== PAGE 11 ===", "=== PAGE 12 ==="]
    assert sum(1 for p in content if p["type"] == "image_url") == 2
    assert rec.kwargs["response_format"] == {"type": "json_object"}
    assert (ai.usage.calls, ai.usage.input_tokens, ai.usage.output_tokens) == (1, 4000, 6000)


def test_claude_sends_one_pdf_per_page_and_counts_tokens():
    from app.providers.claude import ClaudeProvider

    ai = ClaudeProvider("k", "claude-sonnet-5-5")
    rec = Recorder(SimpleNamespace(
        content=[SimpleNamespace(type="text", text='{"b": 2}')],
        usage=SimpleNamespace(input_tokens=5000, output_tokens=4000, cache_read_input_tokens=0,
                              cache_creation_input_tokens=0),
    ))
    ai._client = SimpleNamespace(messages=rec)
    assert ai.generate(DOC, "TASK: LESSON", repair="corrige") == '{"b": 2}'
    content = rec.kwargs["messages"][0]["content"]
    assert [p["text"] for p in content if p["type"] == "text"][:1] == ["=== PAGE 11 ==="]
    assert sum(1 for p in content if p["type"] == "document") == 2
    assert any(p.get("text") == "corrige" for p in content)
    assert rec.kwargs["system"] == "TASK: LESSON"
    assert (ai.usage.input_tokens, ai.usage.output_tokens) == (5000, 4000)


def test_mistral_vision_sends_images_instead_of_ocr():
    from app.providers.mistral import MistralProvider

    ai = MistralProvider("k", "mistral-medium-latest", "mistral-ocr-latest", mode="vision")
    rec = Recorder(SimpleNamespace(
        choices=[SimpleNamespace(message=SimpleNamespace(content="{}"))],
        usage=SimpleNamespace(prompt_tokens=3000, completion_tokens=2000),
    ))
    ai._client = SimpleNamespace(chat=SimpleNamespace(complete=rec.create),
                                 ocr=SimpleNamespace(process=lambda **k: pytest.fail("pas d'OCR en mode vision")))
    ai.generate(DOC, "TASK: LESSON")
    content = rec.kwargs["messages"][1]["content"]
    assert sum(1 for p in content if p["type"] == "image_url") == 2
    assert ai.usage.ocr_pages == 0 and ai.usage.input_tokens == 3000


def test_mistral_ocr_mode_counts_ocr_pages():
    from app.providers.mistral import MistralProvider

    ai = MistralProvider("k", "mistral-medium-latest", "mistral-ocr-latest")
    pages = [SimpleNamespace(markdown="p1"), SimpleNamespace(markdown="p2")]
    ocr = SimpleNamespace(pages=pages, usage_info=SimpleNamespace(pages_processed=2))
    rec = Recorder(SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(content="{}"))],
                                   usage=SimpleNamespace(prompt_tokens=1000, completion_tokens=500)))
    ai._client = SimpleNamespace(chat=SimpleNamespace(complete=rec.create), ocr=SimpleNamespace(process=lambda **k: ocr))
    ai.generate(DOC, "TASK: LESSON")
    assert "=== PAGE 11 ===" in rec.kwargs["messages"][1]["content"]
    assert ai.usage.ocr_pages == 2


def test_a_whole_book_is_refused_by_image_providers():
    from app.providers.gpt import GPTProvider

    ai = GPTProvider("k", "gpt-6.1-sol")
    ai._client = SimpleNamespace(chat=SimpleNamespace(completions=Recorder(None)))
    big = SourceDocument(filename="b.pdf", mime_type="application/pdf", data=pdf(101))
    with pytest.raises(ProviderError, match="Gemini"):
        ai.generate(big, "TASK: OUTLINE")


def test_missing_keys_are_clear(monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "")
    settings = get_settings()
    for name in ("openai", "anthropic"):
        with pytest.raises(ProviderError, match="API_KEY manquante"):
            get_provider(settings, 0, name)
    assert {"openai", "anthropic"} <= set(PROVIDERS)


def test_no_credit_left_stops_at_once():
    class Err(Exception):
        status_code = 400

    err = wrap_error("Claude", Err("Your credit balance is too low to access the Anthropic API."))
    assert isinstance(err, QuotaExhaustedError)


def test_json_with_a_sentence_around_is_still_read():
    assert parse_json('Voici le JSON :\n{"x": 1}\nBonne journée') == {"x": 1}


def test_real_cost_from_tokens():
    usage = Usage(calls=1, input_tokens=1_000_000, output_tokens=1_000_000)
    assert cost_of("gemini-3.8-flash", usage) == pytest.approx(0.75 + 3.75)
    assert cost_of("claude-haiku-5-5", usage) == pytest.approx(0.10 + 0.50)
    assert cost_of("mistral-medium-latest", Usage(input_tokens=0, output_tokens=0, ocr_pages=1000)) == pytest.approx(2.0)
    assert cost_of("modele-inconnu", usage) is None
