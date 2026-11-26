"""Tests for shuffling MCQ options."""
import random

from study_ai import shuffle_options


def make_mcq(answer):
    return {"question": "Q", "options": ["w", "x", "y", "z"], "answer": answer, "subtopic": "S"}


def test_shuffle_keeps_correct_answer_correct():
    rng = random.Random(0)
    for _ in range(200):
        answer = rng.randrange(4)
        original = make_mcq(answer)
        [q] = shuffle_options([original], rng)
        assert q["options"][q["answer"]] == original["options"][answer]
        assert sorted(q["options"]) == sorted(original["options"])
        assert q["subtopic"] == "S"


def test_shuffle_does_not_change_input():
    original = make_mcq(2)
    shuffle_options([original], random.Random(1))
    assert original == make_mcq(2)


def test_shuffle_actually_moves_answers():
    # Gemini often puts the answer at index 0; after shuffling it should land everywhere.
    rng = random.Random(42)
    positions = {shuffle_options([make_mcq(0)], rng)[0]["answer"] for _ in range(100)}
    assert positions == {0, 1, 2, 3}
