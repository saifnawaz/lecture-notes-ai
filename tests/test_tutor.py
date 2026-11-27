"""Tests for the AI tutor: explain, targeted practice, why wrong (Gemini is faked)."""
import pytest
from fastapi.testclient import TestClient

import app as app_module
import db
import study_ai
from quiz import plan_practice


def mcq(question, subtopic, answer=0):
    return {"question": question, "options": ["a", "b", "c", "d"], "answer": answer,
            "subtopic": subtopic, "explanation": ""}


EXPLANATION = {
    "explanation": ["A set is a group of things.", "Order does not matter."],
    "example": {"problem": "Is {1,2} = {2,1}?", "solution": "Yes, order does not matter."},
    "key_points": ["one", "two", "three", "four"],
}


class FakeGemini:
    """Replaces ask_with_fallback: counts calls, keeps the last prompt, returns canned answers."""

    def __init__(self, *answers):
        self.answers = list(answers)
        self.calls = 0
        self.prompt = None

    def __call__(self, client, contents, validate, *args, **kwargs):
        self.calls += 1
        self.prompt = contents[-1]
        answer = self.answers[min(self.calls, len(self.answers)) - 1]
        validate(answer)
        return answer


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setattr(db, "DB_PATH", str(tmp_path / "test.db"))
    monkeypatch.setattr(app_module, "make_client", lambda: None)
    with TestClient(app_module.app) as c:
        yield c


def fake_gemini(monkeypatch, *answers):
    fake = FakeGemini(*answers)
    monkeypatch.setattr(study_ai, "ask_with_fallback", fake)
    return fake


@pytest.fixture
def subject(client):
    """Math with two notes: Sets (2 questions) and Logic (1 question)."""
    db.save_note({"subject": "Math", "topic": "Sets", "notes": ["a set is a collection"],
                  "mcqs": [mcq("What is a set?", "Sets"), mcq("What is a subset?", "Sets")]})
    db.save_note({"subject": "Math", "topic": "Logic", "notes": ["p and q is true if both are"],
                  "mcqs": [mcq("What is p and q?", "Logic")]})
    return client.get("/subjects").json()[0]["id"]


def answer(client, note_id, index, chosen):
    client.post("/attempts", json={"note_id": note_id, "question_index": index, "chosen": chosen})


# ---------- explain ----------

def test_explain_returns_three_key_points_and_uses_notes(client, subject, monkeypatch):
    fake = fake_gemini(monkeypatch, EXPLANATION)
    r = client.post(f"/subjects/{subject}/topics/Sets/explain")
    assert r.status_code == 200
    data = r.json()
    assert data["explanation"] == EXPLANATION["explanation"]
    assert data["example"]["solution"].startswith("Yes")
    assert data["key_points"] == ["one", "two", "three"]  # trimmed to 3
    assert data["cached"] is False
    assert '"Sets"' in fake.prompt and "a set is a collection" in fake.prompt


def test_explain_is_cached_until_regenerate(client, subject, monkeypatch):
    other = {**EXPLANATION, "explanation": ["A new explanation."]}
    fake = fake_gemini(monkeypatch, EXPLANATION, other)
    client.post(f"/subjects/{subject}/topics/Sets/explain")
    again = client.post(f"/subjects/{subject}/topics/sets/explain").json()  # any case
    assert fake.calls == 1 and again["cached"] is True

    fresh = client.post(f"/subjects/{subject}/topics/Sets/explain?regenerate=true").json()
    assert fake.calls == 2 and fresh["cached"] is False
    assert fresh["explanation"] == ["A new explanation."]
    # The regenerated one replaced the old one
    assert client.post(f"/subjects/{subject}/topics/Sets/explain").json()["explanation"] == ["A new explanation."]


def test_explain_topic_with_slash(client, subject, monkeypatch):
    fake_gemini(monkeypatch, EXPLANATION)
    r = client.post(f"/subjects/{subject}/topics/AND%2FOR/explain")
    assert r.status_code == 200 and r.json()["subtopic"] == "AND/OR"


def test_explain_rejects_bad_json_shape():
    with pytest.raises(study_ai.BadResponse):
        study_ai.validate_explanation({**EXPLANATION, "key_points": ["only one"]})
    with pytest.raises(study_ai.BadResponse):
        study_ai.validate_explanation({**EXPLANATION, "example": "not an object"})


def test_explain_unknown_subject(client):
    assert client.post("/subjects/99/topics/Sets/explain").status_code == 404


def test_explain_gemini_failure_is_502_and_not_cached(client, subject, monkeypatch):
    def fail(*args, **kwargs):
        raise study_ai.GenerationError("All models failed.")
    monkeypatch.setattr(study_ai, "ask_with_fallback", fail)
    r = client.post(f"/subjects/{subject}/topics/Sets/explain")
    assert r.status_code == 502 and r.json()["detail"] == "All models failed."
    assert db.get_explanation("Math", "Sets") is None


# ---------- practice ----------

def test_plan_gives_weaker_topics_more():
    plan = plan_practice([("Strong", 0.9), ("Weak", 0.2), ("Mid", 0.5), ("Other", 0.95)], total=8)
    assert [t for t, _ in plan] == ["Weak", "Mid", "Strong"]  # 3 weakest, weakest first
    counts = [n for _, n in plan]
    assert sum(counts) == 8 and counts == sorted(counts, reverse=True) and min(counts) >= 1


def test_plan_edge_cases():
    assert plan_practice([], 8) == []
    assert plan_practice([("A", 0.3)], 5) == [("A", 5)]


def test_practice_saves_new_questions_that_count(client, subject, monkeypatch):
    sets_id, logic_id = [n["id"] for n in sorted(client.get("/notes").json(), key=lambda n: n["id"])]
    answer(client, sets_id, 0, 1)  # Sets: wrong -> weakest
    answer(client, logic_id, 0, 0)  # Logic: right
    fake = fake_gemini(monkeypatch, {"mcqs": [
        mcq("Is the empty set a subset of every set?", "sets", answer=1),
        mcq("What is p and q?", "Logic"),  # a repeat: dropped
        mcq("When is p or q false?", "Logic"),
        mcq("Off-topic question?", "Biology"),  # not requested: dropped
    ]})

    r = client.post(f"/subjects/{subject}/practice?n=5")
    assert r.status_code == 200
    new = r.json()
    assert [q["question"] for q in new] == ["Is the empty set a subset of every set?", "When is p or q false?"]
    assert all(q["practice"] for q in new)
    assert new[0]["subtopic"] == "Sets" and new[0]["note_id"] == sets_id  # planned spelling, right note
    assert new[1]["note_id"] == logic_id

    # The prompt asked for the weakest topic first and listed existing question stems
    assert fake.prompt.index("- Sets:") < fake.prompt.index("- Logic:")
    assert "- What is a subset?" in fake.prompt

    # Answering a practice question counts toward progress
    q = new[0]
    answer(client, q["note_id"], q["question_index"], q["answer"])
    sets = next(p for p in client.get("/progress?subject=Math").json() if p["subtopic"] == "Sets")
    assert sets["attempts"] == 2 and sets["correct"] == 1


def test_practice_needs_questions(client, monkeypatch):
    sid = client.post("/subjects", json={"name": "Empty"}).json()["id"]
    assert client.post(f"/subjects/{sid}/practice").status_code == 400


def test_practice_size_limits(client, subject):
    assert client.post(f"/subjects/{subject}/practice?n=4").status_code == 422
    assert client.post(f"/subjects/{subject}/practice?n=11").status_code == 422


def test_practice_rejects_answers_without_requested_topics(monkeypatch):
    fake_gemini(monkeypatch, {"mcqs": [mcq("Q?", "Biology")]})
    with pytest.raises(study_ai.BadResponse):  # ask_with_fallback would retry on this
        study_ai.generate_practice(None, "Math", [("Sets", 5)], ["n"])


# ---------- why wrong ----------

def test_why_wrong_sends_question_and_is_cached(client, subject, monkeypatch):
    nid = client.get("/notes").json()[-1]["id"]  # Sets note
    fake = fake_gemini(monkeypatch, {"why": "  B is a different idea. A is right because...  "})

    r = client.post("/why", json={"note_id": nid, "question_index": 0, "chosen": 1})
    assert r.json() == {"why": "B is a different idea. A is right because...", "cached": False}
    assert "What is a set?" in fake.prompt
    assert "Student's choice: B) b" in fake.prompt and "Correct answer: A) a" in fake.prompt

    again = client.post("/why", json={"note_id": nid, "question_index": 0, "chosen": 1}).json()
    assert again["cached"] is True and fake.calls == 1
    # Another wrong option is a different cache entry
    client.post("/why", json={"note_id": nid, "question_index": 0, "chosen": 2})
    assert fake.calls == 2


def test_why_rejects_correct_and_invalid_answers(client, subject):
    nid = client.get("/notes").json()[-1]["id"]
    assert client.post("/why", json={"note_id": nid, "question_index": 0, "chosen": 0}).status_code == 400
    assert client.post("/why", json={"note_id": nid, "question_index": 9, "chosen": 1}).status_code == 400
    assert client.post("/why", json={"note_id": 999, "question_index": 0, "chosen": 1}).status_code == 404


def test_why_cache_removed_with_note(client, subject, monkeypatch):
    nid = client.get("/notes").json()[-1]["id"]
    fake_gemini(monkeypatch, {"why": "because"})
    client.post("/why", json={"note_id": nid, "question_index": 0, "chosen": 1})
    client.delete(f"/notes/{nid}")
    assert db.get_why(nid, 0, 1) is None
