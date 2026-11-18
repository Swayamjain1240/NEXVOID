"""Phrase-novelty signal tests (M17): pairing, question-word handling, and the count guard."""

from __future__ import annotations

import pytest

from atlas.phrase import MIN_UNSEEN_PHRASES, content_bigrams, novelty, refuses, unseen_phrases

CORPUS = frozenset({"merit scholarship", "scholarship renewal", "percent attendance"})


def test_pairs_follow_question_order():
    # order matters: "scholarship attendance" is not the same claim as "attendance scholarship".
    # "sports" folds to "sport" through the same tokeniser retrieval uses.
    assert content_bigrams("sports scholarship attendance") == (
        "scholarship attendance",
        "sport scholarship",
    )


def test_question_words_are_dropped_before_pairing():
    # "what is the" must not create pairs like "what hostel"
    assert content_bigrams("What is the hostel curfew?") == ("hostel curfew",)


def test_single_term_question_has_no_pairs():
    assert content_bigrams("attendance") == ()


def test_unseen_pairs_are_reported_and_attested_ones_are_not():
    assert unseen_phrases("merit scholarship renewal", CORPUS) == ()
    assert unseen_phrases("sports scholarship attendance", CORPUS) == (
        "scholarship attendance",
        "sport scholarship",
    )


def test_novelty_is_the_share_of_unseen_pairs():
    assert novelty("sports scholarship attendance", CORPUS) == pytest.approx(1.0)
    assert novelty("merit scholarship renewal", CORPUS) == 0.0
    assert novelty("merit scholarship", CORPUS) == 0.0
    assert novelty("attendance", CORPUS) == 0.0  # nothing to pair: no evidence, not novelty


def test_floor_zero_disables_the_signal():
    assert not refuses(novel=1.0, unseen=9, floor=0.0)


def test_one_unseen_pair_is_not_enough():
    assert not refuses(novel=1.0, unseen=MIN_UNSEEN_PHRASES - 1, floor=0.5)


def test_two_unseen_pairs_at_the_floor_refuse():
    assert refuses(novel=0.5, unseen=2, floor=0.5)
    assert not refuses(novel=0.49, unseen=2, floor=0.5)
