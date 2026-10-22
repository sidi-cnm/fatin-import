import json

from fastapi.testclient import TestClient

from app.main import app
from app.pipeline import run_pipeline
from app.providers.base import AIProvider, SourceDocument

client = TestClient(app)
HEADERS = {"X-API-Key": "test-key"}
FAKE_PDF = b"%PDF-1.4\n%fake document for tests\n"


def test_health():
    assert client.get("/health").json()["status"] == "ok"


def test_api_key_is_required():
    r = client.post("/api/parse-document/", files={"file": ("u1.pdf", FAKE_PDF)}, data={"subject": "16"})
    assert r.status_code == 401


def test_parse_document_with_fake_provider():
    r = client.post(
        "/api/parse-document/",
        headers=HEADERS,
        files={"file": ("math_U01.pdf", FAKE_PDF, "application/pdf")},
        data={"subject": "42", "stage": "الابتدائية", "level": "السنة الأولى", "subject_name": "الرياضيات"},
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["draft"]["subject"] == 42
    assert body["draft"]["source_file"] == "math_U01.pdf"
    assert body["meta"]["provider"] == "fake"
    assert [i for i in body["issues"] if i["level"] == "error"] == []


def test_french_document_sets_language():
    r = client.post(
        "/api/parse-document/",
        headers=HEADERS,
        files={"file": ("maths_1AF_U01.pdf", FAKE_PDF, "application/pdf")},
        data={"subject": "20", "language": "fr"},
    )
    assert r.status_code == 200, r.text
    assert r.json()["draft"]["language"] == "fr"


def test_unknown_language_is_refused():
    r = client.post(
        "/api/parse-document/", headers=HEADERS, files={"file": ("u.pdf", FAKE_PDF)},
        data={"subject": "1", "language": "en"},
    )
    assert r.status_code == 422


def test_wrong_file_type():
    r = client.post("/api/parse-document/", headers=HEADERS, files={"file": ("x.png", b"123")}, data={"subject": "1"})
    assert r.status_code == 400


def test_validate_endpoint(sample):
    r = client.post("/api/validate/", headers=HEADERS, json=sample)
    assert r.status_code == 200
    assert r.json()["valid"] is True


class FlakyProvider(AIProvider):
    """1er essai : JSON invalide ; 2e essai : JSON correct -> teste la seconde chance."""

    name, model = "flaky", "test"

    def __init__(self, good: dict):
        self.good, self.calls = good, 0

    def generate(self, doc, instructions, repair=None):
        self.calls += 1
        if self.calls == 1:
            return json.dumps({"subject": 1, "units": [{"title": "sans ref"}]})
        assert repair and "VALIDATION ERRORS" in repair
        return "```json\n" + json.dumps(self.good, ensure_ascii=False) + "\n```"


def test_second_chance_repairs_invalid_json(sample):
    provider = FlakyProvider(sample)
    doc = SourceDocument(filename="u.pdf", mime_type="application/pdf", data=FAKE_PDF)
    result = run_pipeline(doc, provider, subject_id=7, language="ar")
    assert provider.calls == 2
    assert result.meta.repaired is True
    assert result.draft.subject == 7
    assert result.draft.language == "ar"
