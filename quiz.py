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


def plan_practice(topics, total=8, max_topics=3):
    """Split `total` new questions over the weakest topics.

    topics: [(subtopic, mastery)]. The lowest mastery topics are used (up to max_topics),
    every chosen topic gets at least one question, and the rest is shared in proportion
    to weight(mastery), so weaker topics get more. Returns [(subtopic, count)], weakest first.
    """
    chosen = sorted(topics, key=lambda t: t[1])[:max_topics]
    if not chosen or total <= 0:
        return []
    chosen = chosen[:total]  # can't give every topic a question otherwise
    counts = [1] * len(chosen)
    rest = total - len(chosen)
    weights = [weight(m) for _, m in chosen]
    shares = [rest * w / sum(weights) for w in weights]
    for i, share in enumerate(shares):
        counts[i] += int(share)
    # Hand out what rounding down left over, biggest remainder first (ties: weaker topic).
    left = total - sum(counts)
    order = sorted(range(len(chosen)), key=lambda i: (-(shares[i] - int(shares[i])), i))
    for i in order[:left]:
        counts[i] += 1
    # Rounding can leave a stronger topic with more; the weakest always gets the most.
    counts.sort(reverse=True)
    return [(name, n) for (name, _), n in zip(chosen, counts)]
