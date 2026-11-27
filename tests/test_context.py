"""Picking only the notes and PDF text about the requested subtopics."""
import pytest
from fastapi.testclient import TestClient

import app as app_module
import db
import study_ai
from context import keywords, related_context, split_chunks

SETS_TEXT = "A set is a collection of objects.\nThe union of two sets has every element of both sets."
LOGIC_TEXT = "A proposition is true or false.\nA truth table lists every truth value of a proposition."
GRAPH_TEXT = "A graph has vertices and edges.\nThe degree of a vertex is its number of edges."
NOTES = [
    {"notes": ["Sets are collections", "Union joins two sets"], "source_text": SETS_TEXT},
    {"notes": ["Propositions are true or false", "Truth tables list values"], "source_text": LOGIC_TEXT},
    {"notes": ["Graphs have vertices", "Degree counts edges"], "source_text": GRAPH_TEXT},
]


def test_keywords_stem_and_drop_common_words():
    assert keywords("The Relations and a relation") == ["relati", "relati"]


def test_split_chunks_keeps_size_and_all_text():
    text = "\n".join(f"line number {i} with some words" for i in range(50))
    chunks = split_chunks(text, size=100)
    assert all(len(c) <= 100 for c in chunks)
    assert "\n".join(chunks) == text


def test_split_chunks_cuts_very_long_lines():
    assert split_chunks("x" * 250, size=100) == ["x" * 100, "x" * 100, "x" * 50]


def test_only_related_notes_and_chunks():
    bullets, chunks = related_context(NOTES, [("Truth tables", [])])
    assert bullets == ["Truth tables list values"]
    assert chunks == [LOGIC_TEXT]
    assert not any("graph" in c.lower() or "set" in c.lower() for c in chunks)


def test_question_stems_help_find_the_topic():
    # "Graph theory basics" alone matches only "graph"; its questions add "degree" and "vertex".
    bullets, _ = related_context(NOTES, [("Graph theory basics", ["What is the degree of a vertex?"])])
    assert "Degree counts edges" in bullets and "Sets are collections" not in bullets


def test_several_topics_share_the_budget():
    bullets, chunks = related_context(NOTES, [("Sets", []), ("Truth tables", [])])
    assert SETS_TEXT in chunks and LOGIC_TEXT in chunks and GRAPH_TEXT not in chunks
    assert "Union joins two sets" in bullets and "Graphs have vertices" not in bullets


def test_budget_is_respected():
    big = [{"notes": [f"set fact {i} " + "x" * 80 for i in range(40)], "source_text": ""}]
    bullets, _ = related_context(big, [("Sets", [])], note_chars=500)
    assert sum(map(len, bullets)) <= 500 and bullets


def test_no_match_falls_back_to_first_bullets():
    bullets, chunks = related_context(NOTES, [("Quantum chromodynamics", [])])
    assert bullets[0] == "Sets are collections" and chunks == []


def test_old_notes_without_text_still_work():
    bullets, chunks = related_context([{"notes": ["Sets are collections"], "source_text": None}], [("Sets", [])])
    assert bullets == ["Sets are collections"] and chunks == []


# ---------- the routes send only the related part ----------

class Recorder:
    def __init__(self, answer):
        self.answer = answer
        self.prompt = None

    def __call__(self, client, contents, validate, *args, **kwargs):
        self.prompt = contents[-1]
        validate(self.answer)
        return self.answer


@pytest.fixture
def subject_id(monkeypatch):
    monkeypatch.setattr(app_module, "make_client", lambda: None)
    for note, topic in zip(NOTES, ["Sets", "Truth tables", "Graphs"]):
        db.save_note({"subject": "Math", "topic": topic, "notes": note["notes"], "source_text": note["source_text"],
                      "mcqs": [{"question": f"Question on {topic}?", "options": ["a", "b"], "answer": 0,
                                "subtopic": topic, "explanation": ""}]})
    return db.subject_summaries()[0]["id"]


def test_explain_prompt_has_only_that_topic(subject_id, monkeypatch):
    recorder = Recorder({"explanation": ["p"], "example": {"problem": "q", "solution": "s"},
                         "key_points": ["a", "b", "c"]})
    monkeypatch.setattr(study_ai, "ask_with_fallback", recorder)
    with TestClient(app_module.app) as client:
        assert client.post(f"/subjects/{subject_id}/topics/Truth tables/explain").status_code == 200
    assert "truth table lists every truth value" in recorder.prompt
    assert "vertices" not in recorder.prompt.lower() and "union" not in recorder.prompt.lower()


def test_practice_prompt_sends_only_question_stems(subject_id, monkeypatch):
    recorder = Recorder({"mcqs": [{"question": "New?", "options": ["a", "b"], "answer": 0,
                                   "subtopic": "Sets", "explanation": ""}]})
    monkeypatch.setattr(study_ai, "ask_with_fallback", recorder)
    with TestClient(app_module.app) as client:
        assert client.post(f"/subjects/{subject_id}/practice?n=5").status_code == 200
    assert "- Question on Sets?" in recorder.prompt
    assert '"options": ["a", "b"]' not in recorder.prompt


# ---------- "Generate more": the least covered text ----------

from context import least_covered  # noqa: E402


def test_short_text_is_sent_whole():
    assert least_covered(SETS_TEXT, ["What is a set?"], 10_000) == SETS_TEXT


def test_long_text_keeps_the_uncovered_parts():
    text = "\n".join([SETS_TEXT] * 3 + [GRAPH_TEXT])
    picked = least_covered(text, ["What is a set?", "What is the union of two sets?"], 200, size=60)
    assert "vertices" in picked and "union" not in picked
    assert len(picked) <= 200


def test_pieces_bigger_than_the_budget_still_send_something():
    assert least_covered("x" * 500, [], 100, size=300) == "x" * 100
