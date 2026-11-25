"""Tests for /attempts, /weakness and the weakness score math.

Run with:  pytest
"""
import pytest
from fastapi.testclient import TestClient

import db
from app import app


def mcq(subtopic, answer=0):
    return {
        "question": f"Question about {subtopic}",
        "options": ["A", "B", "C", "D"],
        "answer": answer,
        "subtopic": subtopic,
        "explanation": "",
    }


@pytest.fixture
def client(tmp_path, monkeypatch):
    """A test client using a fresh, empty database in a temp folder."""
    monkeypatch.setattr(db, "DB_PATH", str(tmp_path / "test.db"))
    with TestClient(app) as c:  # "with" runs the startup code (init_db)
        yield c


@pytest.fixture
def note_id(client):
    return db.save_note({
        "subject": "Math",
        "topic": "Sets and Logic",
        "notes": ["a note"],
        "mcqs": [mcq("Sets", answer=0), mcq("Logic", answer=2)],
    })


# ---------- score math ----------

def test_score_with_no_attempts_is_half():
    assert db.weakness_score(wrong=0, attempts=0) == 0.5


def test_score_one_right_one_wrong():
    assert db.weakness_score(wrong=0, attempts=1) == pytest.approx(1 / 3)
    assert db.weakness_score(wrong=1, attempts=1) == pytest.approx(2 / 3)


def test_score_approaches_real_error_rate():
    # 10 wrong out of 100 = 10% real error rate
    assert db.weakness_score(wrong=10, attempts=100) == pytest.approx(11 / 102)


# ---------- POST /attempts ----------

def test_attempt_right_answer(client, note_id):
    r = client.post("/attempts", json={"note_id": note_id, "question_index": 0, "chosen": 0})
    assert r.status_code == 200
    body = r.json()
    assert body["correct"] is True
    assert body["subtopic"] == "Sets"
    assert body["subject"] == "Math"


def test_attempt_wrong_answer(client, note_id):
    r = client.post("/attempts", json={"note_id": note_id, "question_index": 1, "chosen": 0})
    assert r.json()["correct"] is False


def test_attempt_unknown_note(client):
    r = client.post("/attempts", json={"note_id": 999, "question_index": 0, "chosen": 0})
    assert r.status_code == 404


def test_attempt_bad_indexes(client, note_id):
    assert client.post("/attempts", json={"note_id": note_id, "question_index": 9, "chosen": 0}).status_code == 400
    assert client.post("/attempts", json={"note_id": note_id, "question_index": 0, "chosen": 7}).status_code == 400


# ---------- GET /weakness ----------

def answer(client, note_id, question_index, chosen):
    client.post("/attempts", json={"note_id": note_id, "question_index": question_index, "chosen": chosen})


def test_weakness_empty(client):
    assert client.get("/weakness").json() == []


def test_weakness_math_and_order(client, note_id):
    # Sets: 2 right.  Logic: 1 right, 1 wrong.
    answer(client, note_id, 0, 0)
    answer(client, note_id, 0, 0)
    answer(client, note_id, 1, 2)
    answer(client, note_id, 1, 0)

    rows = client.get("/weakness").json()
    assert [r["subtopic"] for r in rows] == ["Logic", "Sets"]  # weakest first

    logic, sets = rows
    assert logic["attempts"] == 2 and logic["wrong"] == 1 and logic["accuracy"] == 50
    assert logic["score"] == pytest.approx(2 / 4, abs=0.001)
    assert sets["attempts"] == 2 and sets["wrong"] == 0 and sets["accuracy"] == 100
    assert sets["score"] == pytest.approx(1 / 4, abs=0.001)


def test_weakness_subject_filter(client, note_id):
    other = db.save_note({"subject": "Physics", "topic": "Motion", "notes": [], "mcqs": [mcq("Speed")]})
    answer(client, note_id, 0, 0)
    answer(client, other, 0, 1)

    rows = client.get("/weakness", params={"subject": "Physics"}).json()
    assert [r["subtopic"] for r in rows] == ["Speed"]
    assert len(client.get("/weakness").json()) == 2


def test_note_accuracy(client, note_id):
    assert client.get(f"/notes/{note_id}").json()["accuracy"] is None
    answer(client, note_id, 0, 0)  # right
    answer(client, note_id, 1, 0)  # wrong
    acc = client.get(f"/notes/{note_id}").json()["accuracy"]
    assert acc == {"attempts": 2, "correct": 1, "accuracy": 50}
