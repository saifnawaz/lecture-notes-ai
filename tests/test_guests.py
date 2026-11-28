"""Guest users: a cookie id per browser, and per-user progress on shared content."""
import sqlite3

import pytest
from fastapi.testclient import TestClient

import db
from app import app


def mcq(subtopic, answer=0):
    return {"question": f"Q {subtopic}", "options": ["A", "B", "C", "D"], "answer": answer, "subtopic": subtopic}


@pytest.fixture
def demo(monkeypatch):
    """Demo mode: a passcode exists, so requests without it are guests."""
    monkeypatch.setenv("ADMIN_PASSCODE", "secret")
    return db.save_note({
        "subject": "Math", "topic": "Sets", "mcqs": [mcq("Sets"), mcq("Logic")],
        "flashcards": [{"front": "f", "back": "b"}],
    })


def guest():
    return TestClient(app)


def test_first_visit_sets_httponly_guest_cookie(demo):
    with guest() as c:
        r = c.get("/subjects")
        cookie = r.headers["set-cookie"]
        assert cookie.startswith("lapse_uid=")
        assert "httponly" in cookie.lower()
        assert "samesite=lax" in cookie.lower()
        # The second request sends the cookie back, so no new one is set.
        assert "set-cookie" not in c.get("/subjects").headers


def test_bad_cookie_is_replaced(demo):
    with guest() as c:
        c.cookies.set("lapse_uid", "not-a-uuid")
        assert "lapse_uid=" in c.get("/subjects").headers["set-cookie"]


def test_guests_do_not_see_each_others_progress(demo):
    with guest() as a, guest() as b:
        a.post("/attempts", json={"note_id": demo, "question_index": 1, "chosen": 3})  # wrong
        assert a.get("/progress").json()[0]["subtopic"] == "Logic"
        assert a.get("/today").json()["last_score"]["attempts"] == 1
        assert a.get("/subjects").json()[0]["accuracy"] == 0

        assert b.get("/progress").json() == []
        assert b.get("/today").json()["last_score"] is None
        assert b.get("/subjects").json()[0]["accuracy"] is None
        assert b.get(f"/notes/{demo}").json()["accuracy"] is None
    # The owner's own progress is separate too.
    assert db.progress() == []


def test_card_schedule_is_per_guest(demo):
    with guest() as a, guest() as b:
        card = a.get("/cards/due").json()[0]
        reviewed = a.post(f"/cards/{card['id']}/review", json={"quality": 4}).json()
        assert reviewed["due_date"] > db.today()
        assert a.get("/cards/due").json() == []
        assert a.get("/today").json()["due"] == 0

        assert [c["id"] for c in b.get("/cards/due").json()] == [card["id"]]  # still new for b
        assert b.get("/today").json()["due"] == 1


def test_admin_passcode_uses_owner_progress(demo):
    with guest() as c:
        c.post("/attempts", json={"note_id": demo, "question_index": 0, "chosen": 0},
               headers={"X-Admin-Passcode": "secret"})
        assert c.get("/progress").json() == []  # as a guest: nothing
        assert len(c.get("/progress", headers={"X-Admin-Passcode": "secret"}).json()) == 1
    assert db.progress()[0]["subtopic"] == "Sets"


def test_old_database_progress_moves_to_owner(tmp_path, monkeypatch):
    """A notes.db from before guests: attempts without user_id, schedule on the cards table."""
    path = tmp_path / "old.db"
    old = sqlite3.connect(path)
    old.executescript("""
        CREATE TABLE notes (id INTEGER PRIMARY KEY AUTOINCREMENT, subject TEXT, topic TEXT,
                            notes TEXT, mcqs TEXT, created_at TEXT);
        CREATE TABLE attempts (id INTEGER PRIMARY KEY AUTOINCREMENT, note_id INTEGER, question_index INTEGER,
                               subtopic TEXT, subject TEXT, chosen INTEGER, correct INTEGER, created_at TEXT);
        CREATE TABLE cards (id INTEGER PRIMARY KEY AUTOINCREMENT, note_id INTEGER, front TEXT, back TEXT,
                            subtopic TEXT, subject TEXT, ease REAL DEFAULT 2.5, interval_days INTEGER DEFAULT 0,
                            repetitions INTEGER DEFAULT 0, due_date TEXT, last_reviewed TEXT);
        INSERT INTO notes VALUES (1, 'Math', 'Sets', '[]', '[]', '2026-01-01T00:00:00+00:00');
        INSERT INTO attempts VALUES (1, 1, 0, 'Sets', 'Math', 0, 1, '2026-01-02T00:00:00+00:00');
        INSERT INTO cards VALUES (1, 1, 'f', 'b', 'Sets', 'Math', 2.6, 6, 2, '2999-01-01', '2026-01-02');
        INSERT INTO cards VALUES (2, 1, 'g', 'c', 'Sets', 'Math', 2.5, 0, 0, '2026-01-01', NULL);
    """)
    old.commit()
    old.close()
    monkeypatch.setattr(db, "DB_PATH", str(path))
    db.init_db()

    assert db.progress()[0]["attempts"] == 1  # owner's
    assert db.progress(user_id="guest") == []
    assert db.get_card(1)["interval_days"] == 6  # owner keeps the schedule
    assert [c["id"] for c in db.due_cards()] == [2]
    assert {c["id"] for c in db.due_cards(user_id="guest")} == {1, 2}  # a guest starts fresh
