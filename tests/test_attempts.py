"""Tests for /attempts, /progress and the mastery score math.

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


# ---------- mastery math ----------

def test_mastery_with_no_attempts_is_half():
    assert db.mastery_score(correct=0, attempts=0) == 0.5


def test_mastery_one_right_one_wrong():
    assert db.mastery_score(correct=1, attempts=1) == pytest.approx(2 / 3)
    assert db.mastery_score(correct=0, attempts=1) == pytest.approx(1 / 3)


def test_mastery_approaches_real_accuracy():
    # 90 right out of 100 = 90% real accuracy
    assert db.mastery_score(correct=90, attempts=100) == pytest.approx(91 / 102)


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


# ---------- GET /progress ----------

def answer(client, note_id, question_index, chosen):
    client.post("/attempts", json={"note_id": note_id, "question_index": question_index, "chosen": chosen})


def test_progress_empty(client):
    assert client.get("/progress").json() == []


def test_progress_math_and_order(client, note_id):
    # Sets: 2 right.  Logic: 1 right, 1 wrong.
    answer(client, note_id, 0, 0)
    answer(client, note_id, 0, 0)
    answer(client, note_id, 1, 2)
    answer(client, note_id, 1, 0)

    rows = client.get("/progress").json()
    assert [r["subtopic"] for r in rows] == ["Logic", "Sets"]  # lowest mastery first

    logic, sets = rows
    assert logic["attempts"] == 2 and logic["correct"] == 1
    assert logic["mastery"] == pytest.approx(2 / 4, abs=0.001)
    assert sets["attempts"] == 2 and sets["correct"] == 2
    assert sets["mastery"] == pytest.approx(3 / 4, abs=0.001)
    assert set(sets) == {"subject", "subtopic", "attempts", "correct", "mastery"}


def test_progress_ties_put_more_attempts_first(client, note_id):
    answer(client, note_id, 0, 1)        # Sets: 0 of 1 -> (0+1)/(1+2) = 1/3
    answer(client, note_id, 1, 2)        # Logic: 1 of 4 -> (1+1)/(4+2) = 1/3
    for _ in range(3):
        answer(client, note_id, 1, 0)
    rows = client.get("/progress").json()
    assert rows[0]["mastery"] == rows[1]["mastery"]
    assert [r["subtopic"] for r in rows] == ["Logic", "Sets"]


def test_progress_subject_filter(client, note_id):
    other = db.save_note({"subject": "Physics", "topic": "Motion", "notes": [], "mcqs": [mcq("Speed")]})
    answer(client, note_id, 0, 0)
    answer(client, other, 0, 1)

    rows = client.get("/progress", params={"subject": "Physics"}).json()
    assert [r["subtopic"] for r in rows] == ["Speed"]
    assert len(client.get("/progress").json()) == 2


def test_note_accuracy(client, note_id):
    assert client.get(f"/notes/{note_id}").json()["accuracy"] is None
    answer(client, note_id, 0, 0)  # right
    answer(client, note_id, 1, 0)  # wrong
    acc = client.get(f"/notes/{note_id}").json()["accuracy"]
    assert acc == {"attempts": 2, "correct": 1, "accuracy": 50}
