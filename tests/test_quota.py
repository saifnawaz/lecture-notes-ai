"""Daily limits on new (uncached) AI answers: 10 per guest, 200 for the whole demo."""
import pytest
from fastapi.testclient import TestClient

import app as app_module
import db
import study_ai

ADMIN = {"X-Admin-Passcode": "secret"}


@pytest.fixture
def note_id(monkeypatch):
    monkeypatch.setenv("ADMIN_PASSCODE", "secret")
    monkeypatch.setattr(app_module, "make_client", lambda: None)

    def fake_why(client, question, options, chosen, answer):
        study_ai.before_call("why_wrong")  # a real Gemini call would happen here
        study_ai.before_call("why_wrong")  # a retry: still one call for the limit
        return "because"

    monkeypatch.setattr(app_module, "why_wrong", fake_why)
    mcqs = [{"question": f"Q{i}", "options": ["A", "B", "C", "D"], "answer": 0} for i in range(5)]
    return db.save_note({"subject": "Math", "topic": "Sets", "mcqs": mcqs})


def ask(client, note_id, n, headers=None):
    """The n-th different wrong answer (so each one is a new, uncached question)."""
    body = {"note_id": note_id, "question_index": n // 3, "chosen": n % 3 + 1}
    return client.post("/why", json=body, headers=headers)


def test_guest_gets_10_new_answers_a_day(note_id):
    with TestClient(app_module.app) as c:
        for n in range(10):
            assert ask(c, note_id, n).status_code == 200
        r = ask(c, note_id, 10)
        assert r.status_code == 429
        assert r.json()["detail"] == app_module.GUEST_LIMIT_MESSAGE
        assert ask(c, note_id, 0).json() == {"why": "because", "cached": True}  # saved answers stay free
    with TestClient(app_module.app) as other:
        assert ask(other, note_id, 10).status_code == 200  # another guest has their own 10


def test_whole_demo_limit(note_id, monkeypatch):
    monkeypatch.setattr(app_module, "TOTAL_DAILY_AI_CALLS", 2)
    for n in range(2):
        with TestClient(app_module.app) as c:
            assert ask(c, note_id, n).status_code == 200
    with TestClient(app_module.app) as c:
        r = ask(c, note_id, 2)
        assert r.status_code == 429
        assert r.json()["detail"] == app_module.DEMO_LIMIT_MESSAGE
    assert db.ai_calls_today() == 2


def test_admin_has_no_limit(note_id, monkeypatch):
    monkeypatch.setattr(app_module, "TOTAL_DAILY_AI_CALLS", 0)
    with TestClient(app_module.app) as c:
        assert ask(c, note_id, 0, headers=ADMIN).status_code == 200
    assert db.ai_calls_today() == 0


def test_cached_answer_does_not_count(note_id, monkeypatch):
    monkeypatch.setattr(app_module, "why_wrong", lambda *a: "from the answer cache")  # no before_call
    with TestClient(app_module.app) as c:
        assert ask(c, note_id, 0).status_code == 200
    assert db.ai_calls_today() == 0
