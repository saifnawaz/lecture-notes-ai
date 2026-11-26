"""Tests for DELETE /notes/{id} and its cascade to attempts and flashcards."""
import pytest
from fastapi.testclient import TestClient

import db
from app import app


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setattr(db, "DB_PATH", str(tmp_path / "test.db"))
    with TestClient(app) as c:
        yield c


def make_note(client, subject, subtopic):
    """A note with one MCQ, two flashcards and one quiz answer."""
    nid = db.save_note({
        "subject": subject, "topic": subtopic, "notes": ["n"],
        "mcqs": [{"question": "Q", "options": ["a", "b"], "answer": 0, "subtopic": subtopic}],
        "flashcards": [{"front": "F1", "back": "B1"}, {"front": "F2", "back": "B2"}],
    })
    client.post("/attempts", json={"note_id": nid, "question_index": 0, "chosen": 0})
    return nid


def count(table, note_id):
    with db.connect() as conn:
        return conn.execute(f"SELECT COUNT(*) FROM {table} WHERE note_id = ?", (note_id,)).fetchone()[0]


def test_delete_note(client):
    nid = make_note(client, "Math", "Sets")
    r = client.delete(f"/notes/{nid}")
    assert r.status_code == 200
    assert r.json() == {"deleted": nid}
    assert client.get(f"/notes/{nid}").status_code == 404
    assert client.get("/notes").json() == []


def test_delete_cascades_to_attempts_and_cards(client):
    nid = make_note(client, "Math", "Sets")
    assert count("attempts", nid) == 1 and count("cards", nid) == 2

    client.delete(f"/notes/{nid}")
    assert count("attempts", nid) == 0
    assert count("cards", nid) == 0
    assert client.get("/progress").json() == []
    assert client.get("/cards/due").json() == []
    assert [s["pdf_count"] for s in client.get("/subjects").json()] == [0]  # subject stays, now empty


def test_delete_leaves_other_notes_alone(client):
    gone = make_note(client, "Math", "Sets")
    kept = make_note(client, "Physics", "Motion")

    client.delete(f"/notes/{gone}")
    assert [n["id"] for n in client.get("/notes").json()] == [kept]
    assert count("attempts", kept) == 1 and count("cards", kept) == 2
    assert [r["subtopic"] for r in client.get("/progress").json()] == ["Motion"]
    assert len(client.get("/cards/due").json()) == 2


def test_delete_unknown_note_is_404(client):
    assert client.delete("/notes/999").status_code == 404


def test_delete_twice_is_404(client):
    nid = make_note(client, "Math", "Sets")
    assert client.delete(f"/notes/{nid}").status_code == 200
    assert client.delete(f"/notes/{nid}").status_code == 404
