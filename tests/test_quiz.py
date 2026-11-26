"""Tests for the mixed subject quiz and its weighted question picking."""
import random
from collections import Counter

import pytest
from fastapi.testclient import TestClient

import db
from app import app
from quiz import UNTRIED_MASTERY, pick_questions, weight


def q(subtopic, i=0):
    return {"question": f"{subtopic} {i}", "subtopic": subtopic}


# ---------- pick_questions ----------

def test_weight_is_higher_for_weaker_topics():
    assert weight(0.2) > weight(UNTRIED_MASTERY) > weight(0.9) > 0


def test_picks_n_different_questions():
    questions = [q("A", i) for i in range(10)]
    picked = pick_questions(questions, {}, 4, random.Random(0))
    assert len(picked) == 4
    assert len({p["question"] for p in picked}) == 4


def test_n_bigger_than_pool_returns_all():
    questions = [q("A", i) for i in range(3)]
    assert len(pick_questions(questions, {}, 10, random.Random(0))) == 3


def test_weak_topics_come_up_more_often():
    questions = [q("Weak", i) for i in range(5)] + [q("Strong", i) for i in range(5)]
    mastery = {"weak": 0.2, "strong": 0.9}
    rng = random.Random(1)
    counts = Counter()
    for _ in range(500):
        for p in pick_questions(questions, mastery, 3, rng):
            counts[p["subtopic"]] += 1
    assert counts["Weak"] > 3 * counts["Strong"]
    assert counts["Strong"] > 0  # strong topics still show up sometimes


def test_topic_lookup_ignores_case():
    questions = [q("Sets", i) for i in range(5)] + [q("Logic", i) for i in range(5)]
    rng = random.Random(2)
    counts = Counter()
    for _ in range(300):
        counts.update(p["subtopic"] for p in pick_questions(questions, {"sets": 0.05, "logic": 0.95}, 2, rng))
    assert counts["Sets"] > counts["Logic"]


# ---------- GET /subjects/{id}/quiz ----------

@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setattr(db, "DB_PATH", str(tmp_path / "test.db"))
    with TestClient(app) as c:
        yield c


def mcq(question, subtopic):
    return {"question": question, "options": ["a", "b"], "answer": 0, "subtopic": subtopic, "explanation": "e"}


@pytest.fixture
def math_id(client):
    db.save_note({"subject": "Math", "topic": "Sets PDF", "notes": [],
                  "mcqs": [mcq("S1", "Sets"), mcq("S2", "Sets")]})
    db.save_note({"subject": "Math", "topic": "Logic PDF", "notes": [],
                  "mcqs": [mcq("L1", "Logic"), {**mcq("L2", ""), "subtopic": ""}]})
    db.save_note({"subject": "Physics", "topic": "Motion", "notes": [], "mcqs": [mcq("P1", "Speed")]})
    return next(s["id"] for s in client.get("/subjects").json() if s["name"] == "Math")


def test_subject_quiz_mixes_all_pdfs_of_the_subject(client, math_id):
    items = client.get(f"/subjects/{math_id}/quiz").json()
    assert sorted(i["question"] for i in items) == ["L1", "L2", "S1", "S2"]  # no Physics
    item = next(i for i in items if i["question"] == "L1")
    assert item["note_topic"] == "Logic PDF"
    assert item["options"] == ["a", "b"] and item["answer"] == 0 and item["explanation"] == "e"
    # note_id + question_index work with POST /attempts
    r = client.post("/attempts", json={"note_id": item["note_id"], "question_index": item["question_index"], "chosen": 0})
    assert r.json()["correct"] is True and r.json()["subtopic"] == "Logic"


def test_question_without_subtopic_uses_pdf_topic(client, math_id):
    items = client.get(f"/subjects/{math_id}/quiz").json()
    assert next(i for i in items if i["question"] == "L2")["subtopic"] == "Logic PDF"


def test_subject_quiz_limit_and_topic_filter(client, math_id):
    assert len(client.get(f"/subjects/{math_id}/quiz", params={"n": 2}).json()) == 2
    sets = client.get(f"/subjects/{math_id}/quiz", params={"subtopic": "sets"}).json()
    assert sorted(i["question"] for i in sets) == ["S1", "S2"]


def test_subject_quiz_errors(client, math_id):
    assert client.get("/subjects/999/quiz").status_code == 404
    assert client.get(f"/subjects/{math_id}/quiz", params={"n": 0}).status_code == 422
