"""Tests for "Generate more MCQs / flashcards" (Gemini is faked)."""
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

import app as app_module
import db
import study_ai
from study_ai import normalize, only_new


def mcq(question, answer=0):
    return {"question": question, "options": ["a", "b", "c", "d"], "answer": answer,
            "subtopic": "S", "explanation": ""}


class FakeGemini:
    """Replaces ask_with_fallback: records the prompt and returns a canned answer."""

    def __init__(self, answer):
        self.answer = answer
        self.contents = None

    def __call__(self, client, contents, validate, *args, **kwargs):
        self.contents = contents
        validate(self.answer)
        return self.answer


class FakeClient:
    def __init__(self):
        self.files = self
        self.uploaded = None

    def upload(self, file):
        self.uploaded = Path(file)
        return "uploaded-pdf"


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setattr(db, "DB_PATH", str(tmp_path / "test.db"))
    with TestClient(app_module.app) as c:
        yield c


@pytest.fixture
def fake_client(monkeypatch):
    fc = FakeClient()
    monkeypatch.setattr(app_module, "make_client", lambda: fc)
    return fc


def fake_gemini(monkeypatch, answer):
    fake = FakeGemini(answer)
    monkeypatch.setattr(study_ai, "ask_with_fallback", fake)
    return fake


def old_note(**extra):
    """A note saved before PDFs were kept: no pdf_path."""
    return db.save_note({"subject": "Math", "topic": "Sets", "notes": ["a set is a collection"],
                         "mcqs": [mcq("What is a set?")],
                         "flashcards": [{"front": "Set", "back": "A collection"}], **extra})


# ---------- duplicate detection ----------

def test_normalize_ignores_case_punctuation_and_spaces():
    assert normalize("  What is a SET? ") == normalize("what is a set")


def test_only_new_drops_existing_and_repeats():
    items = [{"q": "What is a set?"}, {"q": "what is a set"}, {"q": "New one"}, {"q": "new one!"}, {"q": ""}]
    assert only_new(items, ["What is a SET?"], "q") == [{"q": "New one"}]


# ---------- more MCQs ----------

def test_more_mcqs_appends_and_skips_duplicates(client, fake_client, monkeypatch):
    nid = old_note()
    client.post("/attempts", json={"note_id": nid, "question_index": 0, "chosen": 0})
    fake = fake_gemini(monkeypatch, {"mcqs": [mcq("what is a set"), mcq("What is a subset?", answer=2)]})

    r = client.post(f"/notes/{nid}/mcqs")
    assert r.status_code == 200
    assert r.json() == {"added": 1, "mcq_count": 2}

    mcqs = client.get(f"/notes/{nid}").json()["mcqs"]
    assert mcqs[0]["question"] == "What is a set?"  # old question kept at index 0
    new = mcqs[1]
    assert new["question"] == "What is a subset?"
    assert new["options"][new["answer"]] == "c"  # shuffled, still correct
    # The prompt listed the existing question and, without a PDF, used the notes text
    prompt = fake.contents[-1]
    assert "- What is a set?" in prompt and "a set is a collection" in prompt
    assert fake_client.uploaded is None


def test_more_mcqs_uses_saved_pdf(client, fake_client, monkeypatch, tmp_path):
    pdf = tmp_path / "lecture.pdf"
    pdf.write_bytes(b"%PDF")
    nid = old_note(pdf_path=str(pdf))
    fake = fake_gemini(monkeypatch, {"mcqs": [mcq("Fresh question?")]})

    client.post(f"/notes/{nid}/mcqs")
    assert fake_client.uploaded == pdf
    assert fake.contents[0] == "uploaded-pdf"
    assert "a set is a collection" not in fake.contents[-1]  # PDF instead of notes text


def test_missing_pdf_file_falls_back_to_notes(client, fake_client, monkeypatch, tmp_path):
    nid = old_note(pdf_path=str(tmp_path / "gone.pdf"))
    fake = fake_gemini(monkeypatch, {"mcqs": [mcq("Fresh question?")]})
    client.post(f"/notes/{nid}/mcqs")
    assert fake_client.uploaded is None
    assert "a set is a collection" in fake.contents[-1]


def test_more_mcqs_unknown_note(client):
    assert client.post("/notes/999/mcqs").status_code == 404


def test_more_mcqs_gemini_failure_is_502(client, fake_client, monkeypatch):
    nid = old_note()

    def fail(*args):
        raise study_ai.GenerationError("All models failed.")
    monkeypatch.setattr(study_ai, "ask_with_fallback", fail)
    r = client.post(f"/notes/{nid}/mcqs")
    assert r.status_code == 502 and r.json()["detail"] == "All models failed."


# ---------- more flashcards ----------

def test_more_flashcards_appends_and_skips_duplicates(client, fake_client, monkeypatch):
    nid = old_note()
    fake = fake_gemini(monkeypatch, {"flashcards": [
        {"front": "set", "back": "dup"},
        {"front": "Subset", "back": "Part of a set"},
        {"front": "Union", "back": "Everything in either set"},
    ]})
    r = client.post(f"/notes/{nid}/flashcards")
    assert r.json() == {"added": 2, "card_count": 3}
    assert "- Set" in fake.contents[-1]

    fronts = [c["front"] for c in client.get(f"/notes/{nid}/cards").json()]
    assert fronts == ["Set", "Subset", "Union"]

    # Same answer again: nothing new
    assert client.post(f"/notes/{nid}/flashcards").json() == {"added": 0, "card_count": 3}


def test_flashcards_for_note_without_any(client, fake_client, monkeypatch):
    nid = db.save_note({"subject": "Math", "topic": "Logic", "notes": ["p and q"], "mcqs": []})
    fake_gemini(monkeypatch, {"flashcards": [{"front": "AND", "back": "both true"}]})
    assert client.post(f"/notes/{nid}/flashcards").json() == {"added": 1, "card_count": 1}


def test_note_cards_unknown_note(client):
    assert client.get("/notes/999/cards").status_code == 404
