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
    monkeypatch.setattr(app_module, "generate", lambda client, path, content=None, text="": result())
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

    def fake(client_, path, content=None, text=""):
        seen["bytes"] = Path(path).read_bytes()
        seen["content"] = content
        return result()
    monkeypatch.setattr(app_module, "generate", fake)
    upload(client)
    assert seen["bytes"] == PDF
    assert seen["content"] == PDF  # the same in-memory bytes go to Gemini


# ---------- the bytes are read once and used for everything ----------

REAL_PDF = b"%PDF-1.7" + bytes(range(256)) * 400 + b"%%EOF"  # ~100 KB, binary
EMPTY_HASH = hashlib.sha256(b"").hexdigest()


def test_saved_file_is_not_empty_and_hash_matches(client):
    note = upload(client, content=REAL_PDF).json()
    saved = Path(note["pdf_path"]).read_bytes()
    assert len(saved) == len(REAL_PDF) > 0
    assert hashlib.sha256(saved).hexdigest() == note["pdf_hash"] != EMPTY_HASH
    assert Path(note["pdf_path"]).name == f"{note['pdf_hash']}.pdf"


def test_empty_upload_is_400(client, monkeypatch, tmp_path):
    called = []
    monkeypatch.setattr(app_module, "generate", lambda *a: called.append(a))
    r = upload(client, content=b"")
    assert r.status_code == 400
    assert r.json()["detail"] == "The uploaded file is empty"
    assert called == []  # never sent to Gemini
    assert not list((tmp_path / "uploads").glob("*.pdf"))


def test_duplicate_check_uses_hash_of_contents(client, monkeypatch):
    seen = {}
    real_find = db.find_note_by_hash

    def spy(subject, pdf_hash):
        seen["hash"] = pdf_hash
        return real_find(subject, pdf_hash)
    monkeypatch.setattr(db, "find_note_by_hash", spy)
    upload(client, content=REAL_PDF)
    assert seen["hash"] == hashlib.sha256(REAL_PDF).hexdigest()


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
    def fail(client_, path, content=None, text=""):
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
