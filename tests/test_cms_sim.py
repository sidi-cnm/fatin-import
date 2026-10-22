"""Simulation du CMS : la page /cms, la revérification du plan et l'enregistrement en fichiers."""
import base64
import json

import pytest
from fastapi.testclient import TestClient

from app.main import app

client = TestClient(app)
HEADERS = {"X-API-Key": "test-key"}
PNG = "data:image/png;base64," + base64.b64encode(b"\x89PNG\r\n\x1a\nfake").decode()
PLAN = {"units": [{"title": "الوحدة 1", "lessons": [
    {"title": "فوق وتحت", "start_page": 7, "end_page": 8},
    {"title": "يمين ويسار", "start_page": 9, "end_page": 10},
    {"title": "داخل وخارج", "start_page": 11, "end_page": 12},
]}]}


@pytest.fixture
def save_dir(tmp_path, monkeypatch):
    monkeypatch.setenv("CMS_SAVE_DIR", str(tmp_path))
    return tmp_path


def test_page_is_served_in_arabic():
    r = client.get("/cms")
    assert r.status_code == 200
    assert 'dir="rtl"' in r.text and "استخراج الخطة" in r.text


def test_check_outline_needs_the_key():
    assert client.post("/api/cms/check-outline/", json={"outline": PLAN, "total_pages": 40}).status_code == 401


def test_check_outline_finds_a_page_overlap():
    plan = json.loads(json.dumps(PLAN))
    plan["units"][0]["lessons"][1]["start_page"] = 7  # commence avant la fin de la leçon 1
    r = client.post("/api/cms/check-outline/", headers=HEADERS, json={"outline": plan, "total_pages": 40})
    assert r.status_code == 200, r.text
    assert "PAGE_OVERLAP" in [i["code"] for i in r.json()]


def linked(sample, name="u1-l1-g1-s1-c3.png"):
    """Le brouillon de l'admin : le 3e chunk a une image liée."""
    final = json.loads(json.dumps(sample))
    c = final["units"][0]["lessons"][0]["goals"][0]["skills"][0]["chunks"][2]
    c["needs_image"], c["image"], c["source_page"], c["image_note"] = True, f"images/{name}", 24, "page 24 : des oiseaux"
    return final


def test_save_writes_the_unit_folder(sample, save_dir):
    final = linked(sample)
    final["units"][0]["lessons"][0]["title"] = "عنوان صححه المشرف"
    r = client.post("/api/cms/save/", headers=HEADERS, json={
        "book": "رياضيات 1AF.pdf", "unit_index": 1, "plan": PLAN, "draft_ai": sample, "final": final,
        "images": {"u1-l1-g1-s1-c3.png": PNG}, "stats": {"kept": 5, "edited": 1},
    })
    assert r.status_code == 200, r.text
    body = r.json()
    folder = save_dir / "رياضيات_1AF" / "unite_1"
    assert body["folder"] == str(folder) and body["validation"]["valid"] is True
    assert sorted(p.name for p in folder.iterdir()) == [
        "brouillon_ia.json", "images", "images.json", "plan.json", "statistiques.json", "version_admin.json"]
    saved = json.loads((folder / "version_admin.json").read_text(encoding="utf-8"))
    assert saved["units"][0]["lessons"][0]["title"] == "عنوان صححه المشرف"
    # le lien : le chunk nomme son fichier, et le fichier existe
    chunk = saved["units"][0]["lessons"][0]["goals"][0]["skills"][0]["chunks"][2]
    assert chunk["image"] == "images/u1-l1-g1-s1-c3.png"
    assert (folder / chunk["image"]).read_bytes().startswith(b"\x89PNG")
    manifest = json.loads((folder / "images.json").read_text(encoding="utf-8"))
    assert manifest[0]["image"] == chunk["image"] and manifest[0]["source_page"] == 24
    assert manifest[0]["chunk"] == "units[0].lessons[0].goals[0].skills[0].chunks[2]"
    stats = json.loads((folder / "statistiques.json").read_text(encoding="utf-8"))
    assert stats["edited"] == 1 and stats["valid"] is True and stats["errors"] == 0 and "saved_at" in stats


@pytest.mark.parametrize("images, problem", [
    ({}, "missing"),                                                     # lien sans fichier
    ({"u1-l1-g1-s1-c3.png": PNG, "autre.png": PNG}, "unused"),           # fichier sans chunk
])
def test_save_refuses_images_that_do_not_match_the_chunks(sample, save_dir, images, problem):
    r = client.post("/api/cms/save/", headers=HEADERS,
                    json={"book": "b.pdf", "unit_index": 1, "draft_ai": sample, "final": linked(sample), "images": images})
    assert r.status_code == 400 and r.json()["detail"][problem]
    assert not (save_dir / "b").exists()  # rien n'est écrit


def test_saving_again_replaces_old_images(sample, save_dir):
    def save(name):
        return client.post("/api/cms/save/", headers=HEADERS, json={
            "book": "b.pdf", "unit_index": 1, "draft_ai": sample, "final": linked(sample, name), "images": {name: PNG}})
    assert save("u1-l1-g1-s1-c3.png").status_code == 200
    assert save("u1-l1-g1-s1-c2.png").status_code == 200
    assert [p.name for p in (save_dir / "b" / "unite_1" / "images").iterdir()] == ["u1-l1-g1-s1-c2.png"]


def test_save_keeps_an_invalid_draft_but_reports_it(sample, save_dir):
    bad = json.loads(json.dumps(sample))
    for o in bad["units"][0]["lessons"][0]["goals"][0]["skills"][0]["chunks"][2]["options"]:
        o["is_correct"] = False
    r = client.post("/api/cms/save/", headers=HEADERS, json={"book": "b.pdf", "unit_index": 2, "draft_ai": sample, "final": bad})
    assert r.status_code == 200
    assert r.json()["validation"]["valid"] is False
    stats = json.loads((save_dir / "b" / "unite_2" / "statistiques.json").read_text(encoding="utf-8"))
    assert stats["valid"] is False and stats["errors"] >= 1


@pytest.mark.parametrize("images", [{"u1-l1-g1-s1-c3.png": "data:text/html;base64,PGI+"}, {"../..": PNG}])
def test_save_refuses_bad_images(sample, save_dir, images):
    r = client.post("/api/cms/save/", headers=HEADERS,
                    json={"book": "b.pdf", "unit_index": 1, "draft_ai": sample, "final": linked(sample), "images": images})
    assert r.status_code == 400


def test_book_name_cannot_escape_the_save_folder(sample, save_dir):
    r = client.post("/api/cms/save/", headers=HEADERS,
                    json={"book": "../../etc/passwd", "unit_index": 1, "draft_ai": sample, "final": sample})
    assert r.status_code == 200
    assert r.json()["folder"].startswith(str(save_dir))


# --- Production : CMS_SIMULATION=false --------------------------------------------------------------
def test_production_hides_the_test_page_and_file_saving(sample, save_dir, monkeypatch):
    monkeypatch.setenv("CMS_SIMULATION", "false")
    assert client.get("/cms").status_code == 404
    r = client.post("/api/cms/save/", headers=HEADERS, json={"book": "b.pdf", "unit_index": 1, "draft_ai": sample, "final": sample})
    assert r.status_code == 404 and not (save_dir / "b").exists()
    # le vrai CMS garde la revérification du plan
    r = client.post("/api/cms/check-outline/", headers=HEADERS, json={"outline": PLAN, "total_pages": 40})
    assert r.status_code == 200
