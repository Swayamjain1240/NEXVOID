"""The expander's own contract (M27): co-occurrence, thresholds, determinism, and the off switch.

The measurement is in `eval/expansion.py`; these tests only pin the parts that must not drift
between runs - which terms are candidates, what a threshold does, and that the shipped default
leaves every query exactly as it was.
"""

from __future__ import annotations

from atlas.config import SETTINGS
from atlas.expand import TermExpander, added_terms, expanded_query

CHUNKS = [
    frozenset({"library", "loan", "renew", "book"}),
    frozenset({"library", "loan", "renew", "book"}),
    frozenset({"library", "loan", "fine", "overdue"}),
    frozenset({"gym", "membership", "fee"}),
    frozenset({"gym", "membership", "fee"}),
]


def test_neighbours_are_terms_that_travel_together() -> None:
    expander = TermExpander(CHUNKS)
    neighbours = expander.neighbours("library", threshold=0.0, limit=10)
    assert "book" in neighbours
    assert "fee" not in neighbours  # never in a chunk with "library"


def test_a_threshold_above_every_pair_returns_nothing() -> None:
    expander = TermExpander(CHUNKS)
    assert expander.neighbours("library", threshold=0.0, limit=10)
    assert expander.neighbours("library", threshold=99.0, limit=10) == ()


def test_order_and_limit_are_stable_across_builds() -> None:
    first = TermExpander(CHUNKS).neighbours("library", threshold=0.0, limit=2)
    second = TermExpander(CHUNKS).neighbours("library", threshold=0.0, limit=2)
    assert first == second
    assert len(first) == 2


def test_a_term_the_corpus_never_uses_has_no_neighbours() -> None:
    expander = TermExpander(CHUNKS)
    assert expander.neighbours("canteen", threshold=0.0, limit=3) == ()


def test_expanded_query_appends_terms_and_is_a_no_op_without_them() -> None:
    expander = TermExpander(CHUNKS)
    out = expanded_query("library loan", expander, threshold=0.0, limit=2)
    assert out.startswith("library loan")
    assert out != "library loan"
    assert added_terms("library loan", out) == tuple(sorted(out.split()[2:]))
    assert expanded_query("library loan", expander, threshold=99.0, limit=2) == "library loan"


def test_expansion_is_off_by_default() -> None:
    assert SETTINGS.expansion_threshold == 0.0
    assert SETTINGS.expansion_terms == 3
