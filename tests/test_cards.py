"""Tests for the flashcard routes."""
from datetime import date, timedelta

import pytest
from fastapi.testclient import TestClient

import app as app_module
import db
from app import app


def flashcard(front, subtopic="Sets"):
    return {"front": front, "back": f"answer to {front}", "subtopic": subtopic}


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setattr(db, "DB_PATH", str(tmp_path / "test.db"))
    with TestClient(app) as c:
        yield c


@pytest.fixture
def note_id(client):
    return db.save_note({
        "subject": "Math", "topic": "Sets", "notes": ["a set is a collection"], "mcqs": [],
        "flashcards": [flashcard("What is a set?"), flashcard("What is a subset?")],
    })


def test_save_note_saves_flashcards(client, note_id):
    assert client.get(f"/notes/{note_id}").json()["card_count"] == 2


def test_broken_flashcards_are_skipped(client):
    nid = db.save_note({"subject": "Math", "topic": "T", "flashcards": [{"front": "only front"}]})
    assert db.count_cards(nid) == 0


def test_new_cards_are_due_today(client, note_id):
    cards = client.get("/cards/due").json()
    assert [c["front"] for c in cards] == ["What is a set?", "What is a subset?"]
    c = cards[0]
    assert c["ease"] == 2.5 and c["interval_days"] == 0 and c["repetitions"] == 0
    assert c["subject"] == "Math" and c["last_reviewed"] is None


def test_due_subject_filter(client, note_id):
    db.save_note({"subject": "Physics", "topic": "Motion", "flashcards": [flashcard("What is speed?")]})
    assert len(client.get("/cards/due").json()) == 3
    physics = client.get("/cards/due", params={"subject": "Physics"}).json()
    assert [c["front"] for c in physics] == ["What is speed?"]


def test_review_good_schedules_card(client, note_id):
    card_id = client.get("/cards/due").json()[0]["id"]
    r = client.post(f"/cards/{card_id}/review", json={"quality": 4})
    assert r.status_code == 200
    card = r.json()
    assert card["repetitions"] == 1 and card["interval_days"] == 1
    assert card["due_date"] == (date.today() + timedelta(days=1)).isoformat()
    assert card["last_reviewed"] is not None
    # No longer due today
    assert card_id not in [c["id"] for c in client.get("/cards/due").json()]


def test_due_includes_overdue_cards(client, note_id):
    card_id = client.get("/cards/due").json()[0]["id"]
    yesterday = (date.today() - timedelta(days=1)).isoformat()
    db.update_card(card_id, 2.5, 1, 1, yesterday)
    assert card_id in [c["id"] for c in client.get("/cards/due").json()]


def test_review_errors(client, note_id):
    assert client.post("/cards/999/review", json={"quality": 4}).status_code == 404
    card_id = client.get("/cards/due").json()[0]["id"]
    assert client.post(f"/cards/{card_id}/review", json={"quality": 9}).status_code == 422


def test_generate_flashcards_for_old_note(client, monkeypatch):
    nid = db.save_note({"subject": "Math", "topic": "Logic", "notes": ["p and q"], "mcqs": []})
    seen = {}

    def fake_generate(client_, subject, topic, notes):
        seen.update(subject=subject, topic=topic, notes=notes)
        return [flashcard("What is AND?", "Logic")]

    monkeypatch.setattr(app_module, "make_client", lambda: None)
    monkeypatch.setattr(app_module, "generate_flashcards", fake_generate)

    r = client.post(f"/notes/{nid}/flashcards")
    assert r.status_code == 200 and r.json() == {"card_count": 1}
    assert seen == {"subject": "Math", "topic": "Logic", "notes": ["p and q"]}
    # Second time is refused, so cards aren't duplicated
    assert client.post(f"/notes/{nid}/flashcards").status_code == 400


def test_generate_flashcards_unknown_note(client):
    assert client.post("/notes/999/flashcards").status_code == 404


def test_two_button_qualities(client, note_id):
    """Daily Revision sends 1 (Bhool gaya) or 4 (Yaad tha)."""
    forgot, remembered = [c["id"] for c in client.get("/cards/due").json()]

    card = client.post(f"/cards/{forgot}/review", json={"quality": 1}).json()
    assert card["interval_days"] == 1 and card["repetitions"] == 0
    assert card["ease"] == pytest.approx(1.96)

    first = client.post(f"/cards/{remembered}/review", json={"quality": 4}).json()
    second = client.post(f"/cards/{remembered}/review", json={"quality": 4}).json()
    assert (first["interval_days"], second["interval_days"]) == (1, 6)
    assert second["ease"] == pytest.approx(2.5)
