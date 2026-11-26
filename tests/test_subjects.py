"""Tests for subject listing, case-insensitive matching and renaming."""
import pytest
from fastapi.testclient import TestClient

import db
from app import app


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setattr(db, "DB_PATH", str(tmp_path / "test.db"))
    with TestClient(app) as c:
        yield c


def note(subject, flashcards=()):
    mcqs = [{"question": "Q", "options": ["a", "b"], "answer": 0, "subtopic": "T", "explanation": ""}]
    return db.save_note({"subject": subject, "topic": "T", "notes": [], "mcqs": mcqs,
                         "flashcards": list(flashcards)})


def test_subjects_endpoint_sorted_and_deduplicated(client):
    note("Physics")
    note("Discrete Mathematics")
    note("discrete mathematics")  # same subject, different case
    note("")
    assert client.get("/subjects").json() == ["Discrete Mathematics", "Physics"]


def test_new_note_reuses_existing_spelling(client):
    note("Discrete Mathematics")
    nid = note("  DISCRETE mathematics ")
    assert db.get_note(nid)["subject"] == "Discrete Mathematics"


def test_filters_ignore_case(client):
    nid = note("Math", flashcards=[{"front": "F", "back": "B"}])
    client.post("/attempts", json={"note_id": nid, "question_index": 0, "chosen": 0})
    assert len(client.get("/cards/due", params={"subject": "MATH"}).json()) == 1
    assert len(client.get("/weakness", params={"subject": "math"}).json()) == 1


def test_rename_merges_subjects_everywhere(client):
    a = note("discrete", flashcards=[{"front": "F", "back": "B"}])
    note("Discrete Mathematics")
    client.post("/attempts", json={"note_id": a, "question_index": 0, "chosen": 0})

    changed = db.rename_subject("Discrete", "discrete mathematics")
    assert changed == {"notes": 1, "attempts": 1, "cards": 1}
    assert client.get("/subjects").json() == ["Discrete Mathematics"]
    assert db.get_note(a)["subject"] == "Discrete Mathematics"
    assert client.get("/cards/due").json()[0]["subject"] == "Discrete Mathematics"
    assert client.get("/weakness").json()[0]["subject"] == "Discrete Mathematics"


def test_rename_can_fix_case_only(client):
    note("discrete")
    db.rename_subject("discrete", "Discrete")
    assert client.get("/subjects").json() == ["Discrete"]


def test_rename_unknown_subject_changes_nothing(client):
    note("Math")
    assert db.rename_subject("Biology", "Math") == {"notes": 0, "attempts": 0, "cards": 0}
