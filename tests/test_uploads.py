"""Tests for saved PDFs, the duplicate check and removing PDFs on delete."""
import hashlib
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

import app as app_module
import db
from study_ai import GenerationError

PDF = b"%PDF-1.4 lecture one"


def result(topic="Sets"):
    return {
        "subject": "AI guess", "topic": topic, "notes": ["n"],
        "mcqs": [{"question": "Q", "options": ["a", "b"], "answer": 0, "subtopic": "S"}],
        "flashcards": [],
    }


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setattr(db, "DB_PATH", str(tmp_path / "test.db"))
    monkeypatch.setattr(app_module, "UPLOAD_DIR", tmp_path / "uploads")
    monkeypatch.setattr(app_module, "make_client", lambda: None)
    monkeypatch.setattr(app_module, "generate", lambda client, path: result())
    with TestClient(app_module.app) as c:
        yield c


def upload(client, subject="Math", content=PDF, name="lecture.pdf", force=False):
    data = {"subject": subject}
    if force:
        data["force"] = "true"
    return client.post("/generate", data=data, files={"file": (name, content, "application/pdf")})


def test_upload_saves_pdf_and_hash(client):
    note = upload(client).json()
    assert note["subject"] == "Math"  # chosen subject wins over the AI's guess
    assert note["filename"] == "lecture.pdf"
    assert note["pdf_hash"] == hashlib.sha256(PDF).hexdigest()
    assert Path(note["pdf_path"]).read_bytes() == PDF


def test_generation_gets_the_saved_pdf(client, monkeypatch):
    seen = {}

    def fake(client_, path):
        seen["bytes"] = Path(path).read_bytes()
        return result()
    monkeypatch.setattr(app_module, "generate", fake)
    upload(client)
    assert seen["bytes"] == PDF


def test_duplicate_in_same_subject_asks_first(client):
    first = upload(client).json()
    r = upload(client, subject="math", name="renamed.pdf")  # same bytes, other name and case
    assert r.status_code == 409
    assert r.json()["duplicate_of"] == first["id"]
    assert "already in Math" in r.json()["detail"]
    assert len(client.get("/notes").json()) == 1


def test_duplicate_with_force_generates_again(client):
    upload(client)
    r = upload(client, force=True)
    assert r.status_code == 200
    assert len(client.get("/notes").json()) == 2


def test_same_pdf_in_other_subject_is_fine(client):
    upload(client, subject="Math")
    assert upload(client, subject="Physics").status_code == 200


def test_failed_generation_removes_pdf(client, monkeypatch, tmp_path):
    def fail(client_, path):
        raise GenerationError("nope")
    monkeypatch.setattr(app_module, "generate", fail)
    assert upload(client).status_code == 502
    assert list((tmp_path / "uploads").glob("*.pdf")) == []


def test_delete_removes_pdf_only_when_unused(client):
    a = upload(client).json()
    b = upload(client, force=True).json()  # same file, shared on disk
    path = Path(a["pdf_path"])

    client.delete(f"/notes/{a['id']}")
    assert path.exists()  # b still uses it
    client.delete(f"/notes/{b['id']}")
    assert not path.exists()
