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
    assert names(client) == ["Discrete Mathematics", "Physics", "Uncategorized"]  # "" -> Uncategorized


def test_new_note_reuses_existing_spelling(client):
    note("Discrete Mathematics")
    nid = note("  DISCRETE mathematics ")
    assert db.get_note(nid)["subject"] == "Discrete Mathematics"


def test_filters_ignore_case(client):
    nid = note("Math", flashcards=[{"front": "F", "back": "B"}])
    client.post("/attempts", json={"note_id": nid, "question_index": 0, "chosen": 0})
    assert len(client.get("/cards/due", params={"subject": "MATH"}).json()) == 1
    assert len(client.get("/progress", params={"subject": "math"}).json()) == 1


def test_rename_merges_subjects_everywhere(client):
    a = note("discrete", flashcards=[{"front": "F", "back": "B"}])
    note("Discrete Mathematics")
    client.post("/attempts", json={"note_id": a, "question_index": 0, "chosen": 0})

    changed = db.rename_subject("Discrete", "discrete mathematics")
    assert changed == {"notes": 1, "attempts": 1, "cards": 1, "explanations": 0}
    assert names(client) == ["Discrete Mathematics"]
    assert db.get_note(a)["subject"] == "Discrete Mathematics"
    assert client.get("/cards/due").json()[0]["subject"] == "Discrete Mathematics"
    assert client.get("/progress").json()[0]["subject"] == "Discrete Mathematics"


def test_rename_can_fix_case_only(client):
    note("discrete")
    db.rename_subject("discrete", "Discrete")
    assert names(client) == ["Discrete"]


def test_rename_unknown_subject_changes_nothing(client):
    note("Math")
    assert db.rename_subject("Biology", "Math") == {"notes": 0, "attempts": 0, "cards": 0, "explanations": 0}


def names(client):
    return [s["name"] for s in client.get("/subjects").json()]


# ---------- subject cards and overview ----------

def test_create_subject(client):
    r = client.post("/subjects", json={"name": "  Physics "})
    assert r.status_code == 200
    s = r.json()
    assert s["name"] == "Physics"
    assert s["pdf_count"] == 0 and s["due_today"] == 0 and s["accuracy"] is None
    # Same name in another case gives back the same subject
    assert client.post("/subjects", json={"name": "physics"}).json()["id"] == s["id"]
    assert names(client) == ["Physics"]


def test_create_subject_rejects_empty_name(client):
    assert client.post("/subjects", json={"name": ""}).status_code == 422
    assert client.post("/subjects", json={"name": "   "}).status_code == 400


def test_subject_summary_counts(client):
    a = note("Math", flashcards=[{"front": "F1", "back": "B1"}, {"front": "F2", "back": "B2"}])
    note("Math")
    client.post("/attempts", json={"note_id": a, "question_index": 0, "chosen": 0})  # right
    client.post("/attempts", json={"note_id": a, "question_index": 0, "chosen": 1})  # wrong

    [math] = client.get("/subjects").json()
    assert math["pdf_count"] == 2
    assert math["due_today"] == 2
    assert math["attempts"] == 2 and math["correct"] == 1 and math["accuracy"] == 50


def test_subject_overview_has_three_weakest(client):
    nid = db.save_note({
        "subject": "Math", "topic": "T", "notes": [],
        "mcqs": [{"question": f"Q{i}", "options": ["a", "b"], "answer": 0, "subtopic": f"Topic {i}"}
                 for i in range(4)],
    })
    for i in range(4):
        for _ in range(i):  # Topic i: i wrong answers, then 1 right
            client.post("/attempts", json={"note_id": nid, "question_index": i, "chosen": 1})
        client.post("/attempts", json={"note_id": nid, "question_index": i, "chosen": 0})

    sid = client.get("/subjects").json()[0]["id"]
    overview = client.get(f"/subjects/{sid}").json()
    assert overview["name"] == "Math"
    assert [t["subtopic"] for t in overview["weakest"]] == ["Topic 3", "Topic 2", "Topic 1"]


def test_subject_overview_unknown(client):
    assert client.get("/subjects/999").status_code == 404


def test_notes_filtered_by_subject(client):
    note("Math", flashcards=[{"front": "F", "back": "B"}])
    note("Physics")
    rows = client.get("/notes", params={"subject": "math"}).json()
    assert [r["subject"] for r in rows] == ["Math"]
    assert rows[0]["mcq_count"] == 1 and rows[0]["card_count"] == 1
    assert len(client.get("/notes").json()) == 2


def test_rename_merges_subject_rows(client):
    note("discrete")
    note("Discrete Mathematics")
    db.rename_subject("discrete", "Discrete Mathematics")
    [s] = client.get("/subjects").json()
    assert s["name"] == "Discrete Mathematics" and s["pdf_count"] == 2


# ---------- migrating an old database ----------

def test_migrate_old_database(tmp_path, monkeypatch):
    """A notes.db from before subjects/PDF columns existed gets upgraded on startup."""
    import sqlite3
    path = tmp_path / "old.db"
    conn = sqlite3.connect(path)
    conn.execute("CREATE TABLE notes (id INTEGER PRIMARY KEY AUTOINCREMENT, subject TEXT, topic TEXT,"
                 " notes TEXT, mcqs TEXT, created_at TEXT)")
    conn.execute("INSERT INTO notes (subject, topic, notes, mcqs, created_at) VALUES"
                 " ('Math', 'A', '[]', '[]', '2026-01-01'), ('math', 'B', '[]', '[]', '2026-01-02'),"
                 " ('', 'C', '[]', '[]', '2026-01-03')")
    conn.commit()
    conn.close()

    monkeypatch.setattr(db, "DB_PATH", str(path))
    db.init_db()
    db.init_db()  # running twice is safe
    assert db.subject_names() == ["Math", "Uncategorized"]
    assert db.get_note(3)["subject"] == "Uncategorized"
    assert db.get_note(1)["pdf_hash"] is None
    assert db.get_note(2)["subject"] == "Math"  # spelling unified
    assert [s["pdf_count"] for s in db.subject_summaries()] == [2, 1]
