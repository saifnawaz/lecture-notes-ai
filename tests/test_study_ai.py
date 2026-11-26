"""Tests for parsing Gemini responses (no real API calls)."""
import json

import pytest
from fastapi.testclient import TestClient

import app as app_module
import db
import study_ai
from study_ai import BadResponse, GenerationError, unwrap, validate_notes


def good_result():
    return {
        "subject": "Math",
        "topic": "Sets",
        "notes": ["a note"],
        "mcqs": [{"question": "Q", "options": ["a", "b", "c", "d"], "answer": 1,
                  "subtopic": "Sets", "explanation": ""}],
        "flashcards": [{"front": "F", "back": "B", "subtopic": "Sets"}],
    }


class FakeResponse:
    def __init__(self, text):
        self.text = text


class FakeClient:
    """Pretends to be genai.Client. Returns the given answers one by one."""

    def __init__(self, answers):
        self.answers = list(answers)
        self.calls = 0
        self.models = self
        self.files = self

    def upload(self, file):
        return "uploaded-file"

    def generate_content(self, model, contents, config):
        self.calls += 1
        answer = self.answers.pop(0) if self.answers else self.last
        self.last = answer
        return FakeResponse(answer if isinstance(answer, str) else json.dumps(answer))


@pytest.fixture(autouse=True)
def no_sleep(monkeypatch):
    monkeypatch.setattr(study_ai.time, "sleep", lambda s: None)


# ---------- unwrap / validate ----------

def test_unwrap_list_with_one_dict():
    assert unwrap([{"topic": "x"}]) == {"topic": "x"}


def test_unwrap_leaves_other_values_alone():
    assert unwrap({"topic": "x"}) == {"topic": "x"}
    assert unwrap([{"a": 1}, {"b": 2}]) == [{"a": 1}, {"b": 2}]


def test_validate_accepts_good_result():
    validate_notes(good_result())


@pytest.mark.parametrize("break_it", [
    lambda d: d.pop("topic"),
    lambda d: d.update(notes="not a list"),
    lambda d: d.pop("mcqs"),
    lambda d: d.update(flashcards=None),
    lambda d: d["mcqs"][0].pop("options"),
    lambda d: d["mcqs"][0].update(answer=7),
])
def test_validate_rejects_bad_shapes(break_it):
    data = good_result()
    break_it(data)
    with pytest.raises(BadResponse):
        validate_notes(data)


def test_validate_rejects_non_object():
    with pytest.raises(BadResponse):
        validate_notes([good_result(), good_result()])


# ---------- generate() with a fake client ----------

def test_generate_unwraps_list_response(tmp_path):
    client = FakeClient([[good_result()]])
    assert study_ai.generate(client, tmp_path / "x.pdf") == good_result()


def test_generate_retries_after_invalid_response(tmp_path):
    client = FakeClient([{"oops": True}, "not json", good_result()])
    assert study_ai.generate(client, tmp_path / "x.pdf") == good_result()
    assert client.calls == 3


def test_generate_gives_up_with_generation_error(tmp_path):
    client = FakeClient([{"oops": True}])  # always invalid
    with pytest.raises(GenerationError):
        study_ai.generate(client, tmp_path / "x.pdf")


def test_generate_flashcards_unwraps_list():
    client = FakeClient([[{"flashcards": [{"front": "F", "back": "B"}]}]])
    assert study_ai.generate_flashcards(client, "Math", "Sets", ["n"]) == [{"front": "F", "back": "B"}]


# ---------- POST /generate never returns 500 ----------

@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setattr(db, "DB_PATH", str(tmp_path / "test.db"))
    monkeypatch.setattr(app_module, "make_client", lambda: None)
    monkeypatch.setattr(app_module, "UPLOAD_DIR", tmp_path / "uploads")
    with TestClient(app_module.app) as c:
        yield c


def upload(client):
    return client.post("/generate", files={"file": ("lecture.pdf", b"%PDF-1.4", "application/pdf")})


def test_generate_route_saves_good_result(client, monkeypatch):
    monkeypatch.setattr(app_module, "generate", lambda c, path: good_result())
    r = upload(client)
    assert r.status_code == 200
    assert r.json()["topic"] == "Sets"


def test_generate_route_generation_error_is_502(client, monkeypatch):
    def fail(c, path):
        raise GenerationError("The AI couldn't produce usable notes.")
    monkeypatch.setattr(app_module, "generate", fail)
    r = upload(client)
    assert r.status_code == 502
    assert r.json()["detail"] == "The AI couldn't produce usable notes."


def test_generate_route_unexpected_error_is_502(client, monkeypatch):
    def crash(c, path):
        raise AttributeError("'list' object has no attribute 'get'")
    monkeypatch.setattr(app_module, "generate", crash)
    r = upload(client)
    assert r.status_code == 502
    assert r.json()["detail"] == app_module.GENERIC_ERROR
