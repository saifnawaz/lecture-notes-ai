"""The answer cache: same feature + same input = no second Gemini call."""
import json
from types import SimpleNamespace

import pytest

import db
import study_ai


class FakeClient:
    """Counts Gemini calls and uploads."""

    def __init__(self, answer):
        self.answer = answer
        self.calls = 0
        self.uploads = 0
        self.models = self
        self.files = self

    def generate_content(self, model, contents, config):
        self.calls += 1
        return SimpleNamespace(text=json.dumps(self.answer))

    def upload(self, file, config=None):
        self.uploads += 1
        return "uploaded-pdf"


@pytest.fixture(autouse=True)
def use_db_cache(monkeypatch):
    monkeypatch.setattr(study_ai, "cache_get", db.cache_get)
    monkeypatch.setattr(study_ai, "cache_put", db.cache_put)


GOOD = {"topic": "Sets", "notes": ["n"], "mcqs": [], "flashcards": []}


def test_key_depends_on_feature_and_input():
    key = study_ai.cache_key("explain", "prompt")
    assert key == study_ai.cache_key("explain", "prompt")
    assert key != study_ai.cache_key("why_wrong", "prompt")
    assert key != study_ai.cache_key("explain", "prompt!")


def test_same_why_wrong_request_calls_gemini_once():
    client = FakeClient({"why": "Because."})
    for _ in range(3):
        assert study_ai.why_wrong(client, "Q?", ["a", "b", "c"], 1, 0) == "Because."
    assert client.calls == 1
    study_ai.why_wrong(client, "Q?", ["a", "b", "c"], 2, 0)  # different choice = different input
    assert client.calls == 2


def test_same_text_generates_once():
    client = FakeClient(GOOD)
    study_ai.generate(client, "a.pdf", b"bytes", text="Sets are collections.")
    study_ai.generate(client, "b.pdf", b"other bytes", text="Sets are collections.")
    assert client.calls == 1
    study_ai.generate(client, "a.pdf", b"bytes", text="Something else.")
    assert client.calls == 2


def test_same_scanned_pdf_is_not_even_uploaded_again():
    client = FakeClient(GOOD)
    study_ai.generate(client, "a.pdf", b"scanned bytes", text="")
    study_ai.generate(client, "renamed.pdf", b"scanned bytes", text="")
    assert client.calls == 1 and client.uploads == 1


def test_fresh_skips_the_cache_and_replaces_it():
    first = FakeClient({"explanation": ["old"], "example": {"problem": "p", "solution": "s"},
                        "key_points": ["a", "b", "c"]})
    study_ai.explain_topic(first, "Math", "Sets", ["n"])
    second = FakeClient({"explanation": ["new"], "example": {"problem": "p", "solution": "s"},
                         "key_points": ["a", "b", "c"]})
    assert study_ai.explain_topic(second, "Math", "Sets", ["n"])["explanation"] == ["old"]  # cached
    assert study_ai.explain_topic(second, "Math", "Sets", ["n"], fresh=True)["explanation"] == ["new"]
    assert second.calls == 1
    assert study_ai.explain_topic(second, "Math", "Sets", ["n"])["explanation"] == ["new"]


def test_a_cached_answer_in_an_old_shape_is_asked_again():
    db.cache_put(study_ai.cache_key("why_wrong", "x"), "why_wrong", {"reason": "old shape"})
    client = FakeClient({"why": "Because."})
    data = study_ai.cached_ask(client, ["x"], study_ai.validate_why, "why_wrong", "x")
    assert data == {"why": "Because."} and client.calls == 1


def test_failed_calls_are_not_cached(monkeypatch):
    monkeypatch.setattr(study_ai.time, "sleep", lambda s: None)
    broken = FakeClient({"oops": True})
    with pytest.raises(study_ai.GenerationError):
        study_ai.why_wrong(broken, "Q?", ["a", "b"], 1, 0)
    good = FakeClient({"why": "Because."})
    assert study_ai.why_wrong(good, "Q?", ["a", "b"], 1, 0) == "Because." and good.calls == 1


def test_more_mcqs_are_not_cached():
    """A repeat request for more questions means "give me different ones"."""
    client = FakeClient({"mcqs": []})
    study_ai.generate_mcqs(client, "Math", "Sets", ["n"], text="Sets are collections.")
    study_ai.generate_mcqs(client, "Math", "Sets", ["n"], text="Sets are collections.")
    assert client.calls == 2
