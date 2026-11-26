"""SM-2 spaced repetition math. No database or web code here, so it's easy to test."""
import math
from datetime import date, timedelta

# Review buttons -> SM-2 quality (0-5 scale)
QUALITY = {"again": 1, "hard": 3, "good": 4, "easy": 5}
MIN_EASE = 1.3


def sm2(quality, ease, interval_days, repetitions, today=None):
    """Apply one review to a card.

    Returns a dict with the new ease, interval_days, repetitions and due_date (a date).
    """
    if not 0 <= quality <= 5:
        raise ValueError("quality must be between 0 and 5")
    today = today or date.today()

    if quality < 3:
        # Forgot it: start the card over, see it again tomorrow.
        repetitions = 0
        interval_days = 1
    else:
        repetitions += 1
        if repetitions == 1:
            interval_days = 1
        elif repetitions == 2:
            interval_days = 6
        else:
            # Normal rounding (2.5 -> 3). Python's round() would give 2.
            interval_days = math.floor(interval_days * ease + 0.5)

    q = 5 - quality
    ease = max(MIN_EASE, ease + 0.1 - q * (0.08 + q * 0.02))

    return {
        "ease": round(ease, 4),
        "interval_days": interval_days,
        "repetitions": repetitions,
        "due_date": today + timedelta(days=interval_days),
    }
