"""The stratified course sample.

Taking the top N admitted mutations by score produced a course with no easy end
at all -- "ordered easiest first" started in the middle. These pin the sample
down: every bucket that has anything in it is represented, the easiest and the
hardest admitted mutation both survive a sample, and --all is the identity.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

from demo_describe import _allocate, bucket_of, select_challenges  # noqa: E402


def record(score: float, lineno: int = 1, path: str = "m.py") -> dict:
    return {"site": {"path": path, "lineno": lineno}, "score_breakdown": {"score": score}}


@pytest.mark.parametrize(
    "score,expected",
    [(3.0, "easy"), (4.49, "easy"), (4.5, "medium"), (6.5, "medium"), (6.51, "hard"), (10.0, "hard")],
)
def test_bucket_edges(score, expected):
    assert bucket_of(score) == expected


def test_allocate_is_proportional_and_exact():
    take = _allocate({"easy": 4, "medium": 35, "hard": 17}, 40)
    assert sum(take.values()) == 40
    assert take["easy"] >= 1  # the whole point: 4/56 must not round to zero
    assert take["medium"] > take["hard"] > take["easy"]


def test_allocate_never_exceeds_a_bucket():
    take = _allocate({"easy": 1, "medium": 2, "hard": 50}, 20)
    assert take["easy"] <= 1 and take["medium"] <= 2
    assert sum(take.values()) == 20


def test_allocate_caps_at_the_pool():
    assert sum(_allocate({"easy": 2, "medium": 3, "hard": 1}, 40).values()) == 6


def test_sample_keeps_the_easy_end():
    admitted = [record(3.2), record(3.3), record(3.8), record(3.9)]
    admitted += [record(5.0 + i * 0.04, lineno=i) for i in range(35)]
    admitted += [record(6.6 + i * 0.05, lineno=100 + i) for i in range(17)]

    chosen = select_challenges(admitted, 40)
    assert len(chosen) == 40
    labels = [bucket_of(r["score_breakdown"]["score"]) for r in chosen]
    assert labels.count("easy") >= 1
    assert labels.count("medium") >= 1 and labels.count("hard") >= 1
    # easiest first, and the two extremes of the admitted set both survive
    scores = [r["score_breakdown"]["score"] for r in chosen]
    assert scores == sorted(scores)
    assert scores[0] == pytest.approx(3.2)
    assert scores[-1] == pytest.approx(max(r["score_breakdown"]["score"] for r in admitted))


def test_all_is_every_admitted_easiest_first():
    admitted = [record(7.0, 3), record(3.2, 1), record(5.5, 2)]
    chosen = select_challenges(admitted, None)
    assert [r["score_breakdown"]["score"] for r in chosen] == [3.2, 5.5, 7.0]


def test_count_larger_than_admitted_returns_everything():
    admitted = [record(3.2), record(7.0, 2)]
    assert len(select_challenges(admitted, 99)) == 2


def test_ties_break_deterministically():
    admitted = [record(5.0, 9, "b.py"), record(5.0, 2, "a.py"), record(5.0, 1, "a.py")]
    chosen = select_challenges(admitted, None)
    assert [(r["site"]["path"], r["site"]["lineno"]) for r in chosen] == [
        ("a.py", 1), ("a.py", 2), ("b.py", 9)
    ]
