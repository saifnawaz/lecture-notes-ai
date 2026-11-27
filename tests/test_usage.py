"""Token usage logging: every Gemini call is saved in ai_usage (Gemini is faked)."""
import json
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

import app as app_module
import db
import study_ai


def usage(prompt, output, thinking=0):
    return SimpleNamespace(prompt_token_count=prompt, candidates_token_count=output,
                           thoughts_token_count=thinking, total_token_count=prompt + output + thinking)


class FakeClient:
    """Pretends to be genai.Client; every answer reports the given token counts."""

    def __init__(self, answers, meta):
        self.answers = list(answers)
        self.meta = meta
        self.models = self
        self.files = self

    def upload(self, file, config=None):
        return "uploaded-file"

    def generate_content(self, model, contents, config):
        answer = self.answers.pop(0)
        text = answer if isinstance(answer, str) else json.dumps(answer)
        return SimpleNamespace(text=text, usage_metadata=self.meta)


@pytest.fixture(autouse=True)
def no_sleep(monkeypatch):
    monkeypatch.setattr(study_ai.time, "sleep", lambda s: None)


def rows():
    with db.connect() as conn:
        return [dict(r) for r in conn.execute("SELECT * FROM ai_usage ORDER BY id")]


def test_read_usage_handles_missing_metadata():
    assert study_ai.read_usage(SimpleNamespace()) == {
        "prompt_tokens": 0, "output_tokens": 0, "thinking_tokens": 0, "total_tokens": 0}
    meta = SimpleNamespace(prompt_token_count=10, candidates_token_count=None,
                           thoughts_token_count=None, total_token_count=10)
    assert study_ai.read_usage(SimpleNamespace(usage_metadata=meta))["output_tokens"] == 0


def test_every_call_is_logged_with_its_feature(monkeypatch):
    monkeypatch.setattr(study_ai, "usage_hook", db.log_usage)
    client = FakeClient(["not json", {"why": "Because."}], usage(100, 20, 5))
    assert study_ai.why_wrong(client, "Q?", ["a", "b"], 1, 0) == "Because."

    logged = rows()
    assert len(logged) == 2  # the broken answer cost tokens too
    assert all(r["feature"] == "why_wrong" for r in logged)
    assert logged[0]["prompt_tokens"] == 100 and logged[0]["output_tokens"] == 20
    assert logged[0]["thinking_tokens"] == 5 and logged[0]["total_tokens"] == 125
    assert logged[0]["model"] and logged[0]["created_at"]


def test_generate_and_more_use_their_feature_names(monkeypatch, tmp_path):
    monkeypatch.setattr(study_ai, "usage_hook", db.log_usage)
    good = {"topic": "T", "notes": ["n"], "mcqs": [], "flashcards": []}
    client = FakeClient([good, {"flashcards": []}, {"mcqs": []}], usage(1, 1))
    study_ai.generate(client, tmp_path / "x.pdf")
    study_ai.generate_flashcards(client, "S", "T", ["n"])
    study_ai.generate_mcqs(client, "S", "T", ["n"])
    assert [r["feature"] for r in rows()] == ["generate", "flashcards", "more_mcqs"]


def test_logging_error_never_breaks_the_request(monkeypatch):
    def broken(*args):
        raise RuntimeError("database is locked")
    monkeypatch.setattr(study_ai, "usage_hook", broken)
    client = FakeClient([{"why": "Because."}], usage(1, 1))
    assert study_ai.why_wrong(client, "Q?", ["a", "b"], 1, 0) == "Because."


def test_app_logs_to_the_database():
    assert study_ai.usage_hook is db.log_usage  # set when app.py is imported


def test_usage_summary_per_feature():
    with TestClient(app_module.app) as client:
        for tokens in (100, 300):
            db.log_usage("explain", "m", {"prompt_tokens": tokens, "output_tokens": 10,
                                          "thinking_tokens": 0, "total_tokens": tokens + 10})
        db.log_usage("why_wrong", "m", {"prompt_tokens": 50, "output_tokens": 5,
                                        "thinking_tokens": 1, "total_tokens": 56})
        with db.connect() as conn:  # an old call doesn't count as "today"
            conn.execute("""INSERT INTO ai_usage (feature, model, prompt_tokens, output_tokens,
                            thinking_tokens, total_tokens, created_at)
                            VALUES ('why_wrong', 'm', 0, 0, 0, 44, '2020-01-01T00:00:00+00:00')""")
        summary = {r["feature"]: r for r in client.get("/usage").json()}

    explain = summary["explain"]
    assert explain["calls"] == 2 and explain["tokens"] == 420
    assert explain["avg_prompt"] == 200 and explain["avg_total"] == 210
    assert explain["calls_today"] == 2 and explain["tokens_today"] == 420
    why = summary["why_wrong"]
    assert why["calls"] == 2 and why["tokens"] == 100
    assert why["calls_today"] == 1 and why["tokens_today"] == 56
