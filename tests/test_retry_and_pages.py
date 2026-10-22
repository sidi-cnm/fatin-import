import io

import pytest
from pypdf import PdfReader, PdfWriter

from app.pipeline import UnsupportedFile, extract_pdf_pages, run_pipeline
from app.providers.base import AIProvider, ProviderError, SourceDocument, TransientProviderError, wrap_error
from app.providers.fake import FakeProvider

DOC = SourceDocument(filename="u.pdf", mime_type="application/pdf", data=b"%PDF-1.4")


def pdf_bytes(pages: int) -> bytes:
    writer = PdfWriter()
    for _ in range(pages):
        writer.add_blank_page(width=100, height=100)
    buffer = io.BytesIO()
    writer.write(buffer)
    return buffer.getvalue()


# --- Classement des erreurs -----------------------------------------------------------
class FakeSdkError(Exception):
    def __init__(self, code, message):
        super().__init__(message)
        self.code = code


def test_503_high_demand_is_transient():
    err = wrap_error("Gemini", FakeSdkError(503, "This model is currently experiencing high demand."))
    assert isinstance(err, TransientProviderError)


def test_quota_429_is_transient():
    assert isinstance(wrap_error("Gemini", FakeSdkError(429, "RESOURCE_EXHAUSTED")), TransientProviderError)


def test_bad_request_is_not_transient():
    err = wrap_error("Gemini", FakeSdkError(400, "API key not valid"))
    assert isinstance(err, ProviderError) and not isinstance(err, TransientProviderError)


# --- Nouvelles tentatives ---------------------------------------------------------------
class BusyThenOk(AIProvider):
    name, model = "busy", "test"

    def __init__(self, failures: int):
        self.failures, self.calls = failures, 0
        self._ok = FakeProvider(1)

    def generate(self, doc, instructions, repair=None):
        self.calls += 1
        if self.calls <= self.failures:
            raise TransientProviderError("Gemini : 503 UNAVAILABLE")
        return self._ok.generate(doc, instructions, repair)


def test_retries_until_the_server_answers():
    provider, messages = BusyThenOk(failures=2), []
    result = run_pipeline(DOC, provider, 1, retry_attempts=4, retry_wait_seconds=0, progress=messages.append)
    assert provider.calls == 3
    assert result.draft.units
    assert len(messages) == 2 and "essai 1/4" in messages[0]


def test_gives_up_after_the_last_attempt():
    provider = BusyThenOk(failures=10)
    with pytest.raises(TransientProviderError):
        run_pipeline(DOC, provider, 1, retry_attempts=3, retry_wait_seconds=0)
    assert provider.calls == 3


def test_gemini_switches_to_fallback_model():
    from app.providers.gemini import GeminiProvider

    gemini = GeminiProvider("cle-de-test", "modele-principal", fallback_model="modele-secours")
    used = []

    def fake_call(model, doc, instructions, repair):
        used.append(model)
        if model == "modele-principal":
            raise TransientProviderError("503 high demand")
        return "{}"

    gemini._generate_with = fake_call
    assert gemini.generate(DOC, "consignes") == "{}"
    assert used == ["modele-principal", "modele-secours"]
    assert gemini.model == "modele-secours"


# --- Sélection de pages -----------------------------------------------------------------
def test_extract_pages():
    out = extract_pdf_pages(pdf_bytes(10), "2-4,7")
    assert len(PdfReader(io.BytesIO(out)).pages) == 4


@pytest.mark.parametrize("spec", ["0-3", "5-2", "8-12", "abc", ""])
def test_invalid_page_selection(spec):
    with pytest.raises(UnsupportedFile):
        extract_pdf_pages(pdf_bytes(10), spec)


# --- Quota par minute / quota du jour -------------------------------------------------------
def test_daily_quota_is_not_worth_retrying():
    from app.providers.base import QuotaExhaustedError

    err = wrap_error("Gemini", FakeSdkError(429, "RESOURCE_EXHAUSTED quotaId: GenerateRequestsPerDayPerProjectPerModel-FreeTier"))
    assert isinstance(err, QuotaExhaustedError) and not isinstance(err, TransientProviderError)


def test_minute_quota_waits_the_delay_given_by_google():
    err = wrap_error("Gemini", FakeSdkError(429, "RESOURCE_EXHAUSTED ... 'retryDelay': '37s'"))
    assert isinstance(err, TransientProviderError) and err.quota and err.retry_after == 37


def test_429_still_there_after_all_attempts_means_quota_exhausted(monkeypatch):
    from app.pipeline import generate_with_retry
    from app.providers.base import QuotaExhaustedError

    class AlwaysQuota(AIProvider):
        def generate(self, doc, instructions, repair=None):
            raise TransientProviderError("Gemini : 429 RESOURCE_EXHAUSTED", quota=True)

    monkeypatch.setattr("app.pipeline.time.sleep", lambda s: None)
    with pytest.raises(QuotaExhaustedError):
        generate_with_retry(AlwaysQuota(), DOC, "x", None, attempts=3, wait_seconds=0, progress=None)


def test_api_answers_429_when_quota_is_exhausted(monkeypatch):
    from fastapi.testclient import TestClient

    from app.main import app
    from app.providers.base import QuotaExhaustedError
    from app.providers.fake import FakeProvider

    def generate(self, doc, instructions, repair=None):
        raise QuotaExhaustedError("Gemini : quota du jour épuisé.")

    monkeypatch.setattr(FakeProvider, "generate", generate)
    client = TestClient(app)
    r = client.post("/api/parse-document/", headers={"X-API-Key": "test-key"},
                    files={"file": ("u.pdf", pdf_bytes(2), "application/pdf")}, data={"subject": "16"})
    assert r.status_code == 429 and "Quota" in r.text
