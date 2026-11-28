"""Tests for GET /today (the home page "Today" box) and db.last_score."""
import pytest
from fastapi.testclient import TestClient

import db
from app import app


def mcq(subtopic, answer=0):
    return {"question": f"Q {subtopic}", "options": ["A", "B", "C", "D"], "answer": answer, "subtopic": subtopic}


@pytest.fixture
def client():
    with TestClient(app) as c:
        yield c


def test_today_empty(client):
    assert client.get("/today").json() == {"due": 0, "weakest": None, "last_score": None}


def test_today_with_data(client):
    nid = db.save_note({
        "subject": "Math", "topic": "Sets",
        "mcqs": [mcq("Sets"), mcq("Logic")],
        "flashcards": [{"front": "f", "back": "b"}, {"front": "g", "back": "c"}],
    })
    client.post("/attempts", json={"note_id": nid, "question_index": 0, "chosen": 0})  # right
    client.post("/attempts", json={"note_id": nid, "question_index": 1, "chosen": 1})  # wrong

    body = client.get("/today").json()
    assert body["due"] == 2
    assert body["weakest"]["subtopic"] == "Logic"
    assert body["weakest"]["subject_id"] == client.get("/subjects").json()[0]["id"]
    assert body["last_score"]["attempts"] == 2
    assert body["last_score"]["accuracy"] == 50


def test_last_score_uses_latest_day_only():
    nid = db.save_note({"subject": "Math", "topic": "Sets", "mcqs": [mcq("Sets")]})
    with db.connect() as conn:
        conn.execute(
            "INSERT INTO attempts (note_id, question_index, subtopic, subject, chosen, correct, created_at)"
            " VALUES (?, 0, 'Sets', 'Math', 1, 0, '2020-01-01T10:00:00+00:00')", (nid,))
    db.save_attempt(nid, 0, "Sets", "Math", 0, True)
    assert db.last_score()["attempts"] == 1
    assert db.last_score()["accuracy"] == 100
