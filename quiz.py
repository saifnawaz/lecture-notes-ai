"""Pick quiz questions for a subject, showing weak topics more often.

No database or web code here, so it's easy to test.
"""
import random

UNTRIED_MASTERY = 0.5  # a topic you've never answered counts as "don't know yet"


def weight(mastery):
    """Chance weight of a question: lower mastery = higher weight.

    mastery is (correct + 1) / (attempts + 2), so it is always between 0 and 1
    (never exactly 0 or 1) and the weight is never 0: strong topics still show up, just less.
    """
    return 1 - mastery


def pick_questions(questions, mastery_by_topic, n, rng=random):
    """Choose up to n different questions, weighted by weakness.

    questions: dicts with a "subtopic" key.
    mastery_by_topic: {subtopic (lowercase): mastery}.

    Uses weighted sampling without replacement: each question gets the key
    random() ** (1 / weight), and the n biggest keys win. A bigger weight pushes
    the key closer to 1, so weak questions usually win, but not always.
    """
    def key(q):
        mastery = mastery_by_topic.get(q["subtopic"].casefold(), UNTRIED_MASTERY)
        return rng.random() ** (1 / weight(mastery))

    return sorted(questions, key=key, reverse=True)[:n]
