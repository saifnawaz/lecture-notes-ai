"""Per-feature model choice, output limits and thinking level (no real API calls)."""
import json
from types import SimpleNamespace

import pytest

import study_ai


@pytest.fixture(autouse=True)
def no_sleep(monkeypatch):
    monkeypatch.setattr(study_ai.time, "sleep", lambda s: None)


class FakeClient:
    """Records the model and config of each call. Can refuse thinking settings like an old model."""

    def __init__(self, answer, refuse_thinking=False):
        self.answer = answer
        self.refuse_thinking = refuse_thinking
        self.calls = []
        self.models = self

    def generate_content(self, model, contents, config):
        self.calls.append((model, config))
        if self.refuse_thinking and config.thinking_config is not None:
            raise study_ai.errors.ClientError(400, {"error": {
                "code": 400, "status": "INVALID_ARGUMENT", "message": "thinking_level is not supported"}})
        return SimpleNamespace(text=json.dumps(self.answer))


def test_small_tasks_try_the_lite_model_first():
    assert study_ai.models_for("why_wrong")[0] == study_ai.LITE_MODEL
    assert study_ai.models_for("flashcards")[0] == study_ai.LITE_MODEL
    assert study_ai.models_for("generate")[0] == study_ai.MODEL
    models = study_ai.models_for("why_wrong")
    assert len(models) == len(set(models))  # the lite model isn't tried twice


def test_each_feature_has_its_limits():
    for feature, task in study_ai.TASKS.items():
        config = study_ai.config_for(feature)
        assert config.max_output_tokens == task["max_tokens"]
        assert config.response_mime_type == "application/json"
    assert study_ai.config_for("why_wrong").thinking_config.thinking_level.value == "MINIMAL"
    assert study_ai.TASKS["why_wrong"]["max_tokens"] < study_ai.TASKS["generate"]["max_tokens"]


def test_why_wrong_uses_lite_model_with_minimal_thinking():
    client = FakeClient({"why": "Because."})
    study_ai.why_wrong(client, "Q?", ["a", "b"], 1, 0)
    model, config = client.calls[0]
    assert model == study_ai.LITE_MODEL
    assert config.max_output_tokens == study_ai.TASKS["why_wrong"]["max_tokens"]


def test_model_without_thinking_setting_is_asked_again_without_it():
    client = FakeClient({"why": "Because."}, refuse_thinking=True)
    assert study_ai.why_wrong(client, "Q?", ["a", "b"], 1, 0) == "Because."
    (first_model, first), (second_model, second) = client.calls
    assert first_model == second_model  # same model, not a fallback
    assert first.thinking_config is not None and second.thinking_config is None
    assert second.max_output_tokens == first.max_output_tokens
