"""Admin passcode: editing shared content needs X-Admin-Passcode when ADMIN_PASSCODE is set."""
import pytest
from fastapi.testclient import TestClient

import app as app_module
import db

ADMIN = {"X-Admin-Passcode": "secret"}


@pytest.fixture
def client(monkeypatch):
    monkeypatch.setenv("ADMIN_PASSCODE", "secret")
    monkeypatch.setattr(app_module.time, "sleep", lambda s: None)
    with TestClient(app_module.app) as c:
        yield c


@pytest.fixture
def note_id():
    return db.save_note({"subject": "Math", "topic": "Sets", "notes": ["n"], "mcqs": []})


def edits(note_id):
    sid = db.create_subject("Math")
    return [
        ("post", "/generate", {"files": {"file": ("a.pdf", b"%PDF", "application/pdf")}}),
        ("post", "/subjects", {"json": {"name": "Physics"}}),
        ("patch", f"/subjects/{sid}", {"json": {"name": "Maths"}}),
        ("post", f"/notes/{note_id}/mcqs", {}),
        ("post", f"/notes/{note_id}/flashcards", {}),
        ("delete", f"/notes/{note_id}", {}),
        ("post", f"/subjects/{sid}/topics/Sets/explain?regenerate=true", {}),
    ]


def test_guests_cannot_edit(client, note_id):
    for method, url, kwargs in edits(note_id):
        r = getattr(client, method)(url, **kwargs)
        assert r.status_code == 403, url
        assert r.json()["detail"] == "This needs the admin passcode."
    wrong = {"X-Admin-Passcode": "nope"}
    assert client.post("/subjects", json={"name": "Physics"}, headers=wrong).status_code == 403
    assert db.get_note(note_id) is not None
    assert db.subject_names() == ["Math"]


def test_admin_can_create_rename_and_delete(client, note_id):
    assert client.post("/subjects", json={"name": "Physics"}, headers=ADMIN).status_code == 200
    sid = db.create_subject("Math")
    r = client.patch(f"/subjects/{sid}", json={"name": "Maths"}, headers=ADMIN)
    assert r.json()["name"] == "Maths"
    assert db.get_note(note_id)["subject"] == "Maths"
    assert client.delete(f"/notes/{note_id}", headers=ADMIN).status_code == 200


def test_me_and_check(client):
    assert client.get("/me").json() == {"admin": False, "passcode_set": True}
    assert client.get("/me", headers=ADMIN).json() == {"admin": True, "passcode_set": True}
    assert client.post("/admin/check", headers={"X-Admin-Passcode": "nope"}).status_code == 403
    assert client.post("/admin/check", headers=ADMIN).json() == {"admin": True}


def test_without_passcode_local_is_admin_production_is_not(monkeypatch):
    monkeypatch.delenv("ADMIN_PASSCODE", raising=False)
    with TestClient(app_module.app) as c:
        assert c.get("/me").json()["admin"] is True  # local SQLite: you are the admin
        monkeypatch.setattr(db, "is_postgres", lambda: True)
        assert c.get("/me").json()["admin"] is False  # production without a passcode: nobody
        assert c.post("/subjects", json={"name": "X"}).status_code == 403
