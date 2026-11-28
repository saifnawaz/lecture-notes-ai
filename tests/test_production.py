"""Production rules: no files written, and the 4 MB upload limit."""
import pytest
from fastapi.testclient import TestClient

import app as app_module

PDF = b"%PDF-1.4 tiny test pdf"


def result():
    return {"subject": "Math", "topic": "Sets", "notes": ["n"],
            "mcqs": [{"question": "Q", "options": ["A", "B", "C", "D"], "answer": 0}], "flashcards": []}


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setattr(app_module, "UPLOAD_DIR", tmp_path / "uploads")
    monkeypatch.setattr(app_module, "make_client", lambda: None)
    with TestClient(app_module.app) as c:
        yield c


def test_production_upload_writes_no_file(client, tmp_path, monkeypatch):
    sent = []
    monkeypatch.setattr(app_module, "saves_pdfs", lambda: False)
    monkeypatch.setattr(app_module, "generate",
                        lambda c, path, content=None, text="": sent.append((path, content)) or result())
    r = client.post("/generate", files={"file": ("week1.pdf", PDF, "application/pdf")}, data={"subject": "Math"})
    assert r.status_code == 200
    assert r.json()["pdf_path"] is None
    assert not (tmp_path / "uploads").exists()
    assert sent == [("week1.pdf", PDF)]  # Gemini gets the bytes from memory


def test_pdf_over_4_mb_is_refused(client, monkeypatch):
    called = []
    monkeypatch.setattr(app_module, "generate", lambda *a, **k: called.append(a))
    big = b"%PDF" + b"0" * (4 * 1024 * 1024 + 1)
    r = client.post("/generate", files={"file": ("big.pdf", big, "application/pdf")})
    assert r.status_code == 413
    assert r.json()["detail"] == "This PDF is 4.1 MB. The limit is 4 MB."
    assert called == []


def test_health_reports_database(client):
    assert client.get("/health").json() == {"ok": True, "db": "sqlite"}


def test_health_when_database_is_down(client, monkeypatch):
    def broken():
        raise RuntimeError("no database")
    monkeypatch.setattr(app_module.db, "connect", broken)
    r = client.get("/health")
    assert r.status_code == 503
    assert r.json() == {"ok": False, "db": "sqlite"}
