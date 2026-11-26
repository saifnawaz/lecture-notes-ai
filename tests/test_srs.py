"""Tests for the SM-2 math in srs.py."""
from datetime import date

import pytest

from srs import MIN_EASE, QUALITY, sm2

TODAY = date(2026, 1, 10)
NEW_CARD = {"ease": 2.5, "interval_days": 0, "repetitions": 0}


def review(card, quality):
    return sm2(quality, card["ease"], card["interval_days"], card["repetitions"], today=TODAY)


def test_button_qualities():
    assert QUALITY == {"again": 1, "hard": 3, "good": 4, "easy": 5}


# ---------- one review of a new card, for each button ----------

def test_again_on_new_card():
    r = review(NEW_CARD, 1)
    assert r["repetitions"] == 0
    assert r["interval_days"] == 1
    assert r["ease"] == pytest.approx(2.5 - 0.54)  # 0.1 - 4 * (0.08 + 4 * 0.02)
    assert r["due_date"] == date(2026, 1, 11)


def test_hard_on_new_card():
    r = review(NEW_CARD, 3)
    assert r["repetitions"] == 1
    assert r["interval_days"] == 1
    assert r["ease"] == pytest.approx(2.5 - 0.14)  # 0.1 - 2 * (0.08 + 2 * 0.02)


def test_good_on_new_card():
    r = review(NEW_CARD, 4)
    assert r["repetitions"] == 1
    assert r["interval_days"] == 1
    assert r["ease"] == pytest.approx(2.5)  # Good leaves ease unchanged


def test_easy_on_new_card():
    r = review(NEW_CARD, 5)
    assert r["repetitions"] == 1
    assert r["interval_days"] == 1
    assert r["ease"] == pytest.approx(2.6)


# ---------- each button on a mature card ----------

MATURE = {"ease": 2.5, "interval_days": 10, "repetitions": 3}


@pytest.mark.parametrize("quality, interval, reps, ease", [
    (1, 1, 0, 1.96),   # forgot: back to 1 day
    (3, 25, 4, 2.36),  # interval uses the old ease: 10 * 2.5
    (4, 25, 4, 2.5),
    (5, 25, 4, 2.6),
])
def test_each_quality_on_mature_card(quality, interval, reps, ease):
    r = review(MATURE, quality)
    assert r["interval_days"] == interval
    assert r["repetitions"] == reps
    assert r["ease"] == pytest.approx(ease)
    assert (r["due_date"] - TODAY).days == interval


def test_quality_out_of_range():
    with pytest.raises(ValueError):
        review(NEW_CARD, 6)


# ---------- several reviews in a row ----------

def test_interval_growth_with_good():
    card = dict(NEW_CARD)
    intervals = []
    for _ in range(5):
        card = review(card, 4)
        intervals.append(card["interval_days"])
    # 1, 6, then x2.5 each time: 15, 37.5 -> 38, 95
    assert intervals == [1, 6, 15, 38, 95]
    assert card["ease"] == pytest.approx(2.5)


def test_again_resets_progress():
    card = dict(NEW_CARD)
    for _ in range(3):
        card = review(card, 4)
    card = review(card, 1)
    assert card["repetitions"] == 0 and card["interval_days"] == 1
    card = review(card, 4)
    assert card["interval_days"] == 1  # starts the 1, 6, ... ladder again
    card = review(card, 4)
    assert card["interval_days"] == 6


def test_ease_never_below_minimum():
    card = dict(NEW_CARD)
    for _ in range(20):
        card = review(card, 1)
        assert card["ease"] >= MIN_EASE
    assert card["ease"] == pytest.approx(MIN_EASE)


def test_hard_repeatedly_floors_ease():
    card = dict(NEW_CARD)
    for _ in range(20):
        card = review(card, 3)
        assert card["ease"] >= MIN_EASE
    assert card["ease"] == pytest.approx(MIN_EASE)
