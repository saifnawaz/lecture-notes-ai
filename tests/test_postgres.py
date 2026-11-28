"""Smoke test against a real Postgres. Runs only when DATABASE_URL is set:

    DATABASE_URL=postgresql://... pytest tests/test_postgres.py

It creates tables if needed, adds its own rows under unique names and deletes them at the end.
Use a dev database or a Neon branch, not the live demo.
"""
import os
import uuid

import pytest
from fastapi.testclient import TestClient

import db

URL = os.getenv("DATABASE_URL")
pytestmark = pytest.mark.skipif(not URL, reason="DATABASE_URL not set")


@pytest.fixture
def pg(monkeypatch):
    monkeypatch.setattr(db, "DATABASE_URL", URL)  # conftest switched to SQLite; switch back
    db.init_db()
    db.init_db()  # creating the schema twice must be harmless
    yield


def test_postgres_round_trip(pg):
    tag = uuid.uuid4().hex[:8]
    subject = f"Smoke {tag}"
    user = f"smoke-{tag}"
    nid = db.save_note({
        "subject": subject, "topic": "Sets",
        "notes": ["a note"],
        "mcqs": [{"question": "Q", "options": ["A", "B", "C", "D"], "answer": 1, "subtopic": "Sets"}],
        "flashcards": [{"front": "f", "back": "b", "subtopic": "Sets"}],
    })
    try:
        assert db.create_subject(subject.upper()) == db.create_subject(subject)  # case-insensitive
        sid = db.create_subject(subject)
        assert [n["mcq_count"] for n in db.list_notes(subject)] == [1]

        db.save_attempt(nid, 0, "Sets", subject, 1, True, user_id=user)
        db.save_attempt(nid, 0, "Sets", subject, 0, False, user_id=user)
        assert db.progress(subject, user_id=user)[0]["accuracy"] == 50
        assert db.last_score(user)["attempts"] == 2
        assert db.progress(subject, user_id="someone-else") == []

        card = db.due_cards(subject, user_id=user)[0]
        db.update_card(card["id"], 2.6, 3, 1, "2999-01-01", user_id=user)
        assert db.due_cards(subject, user_id=user) == []
        assert len(db.due_cards(subject, user_id="someone-else")) == 1
        assert db.get_subject(sid, user)["due_today"] == 0

        db.save_explanation(subject, "Sets", {"x": 1})
        db.save_explanation(subject.lower(), "SETS", {"x": 2})  # replaces, any case
        assert db.get_explanation(subject, "sets")["x"] == 2
        db.save_why(nid, 0, 0, "first")
        db.save_why(nid, 0, 0, "second")
        assert db.get_why(nid, 0, 0) == "second"

        db.count_ai_call(user)
        db.count_ai_call(user)
        assert db.ai_calls_today(user) == 2
    finally:
        db.delete_note(nid)
        with db.connect() as conn:
            conn.execute("DELETE FROM subjects WHERE LOWER(name) = LOWER(?)", (subject,))
            conn.execute("DELETE FROM explanations WHERE LOWER(subject) = LOWER(?)", (subject,))
            conn.execute("DELETE FROM ai_quota WHERE user_id = ?", (user,))


def test_postgres_app_starts(pg):
    from app import app
    with TestClient(app) as client:
        assert client.get("/subjects").status_code == 200
        assert client.get("/today").status_code == 200
