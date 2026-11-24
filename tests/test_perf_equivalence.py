"""M22: the cached coverage must equal the tokenising path *exactly*, not approximately.

The speedup removed work, not accuracy: `coverage_of_tokens` reads the token sets the tenant
view caches, while `coverage` re-tokenises the passage. Both must produce the same float for
the same passage, or a "2.2x" would be a quiet change to every confidence score, every gate
decision and every refusal.

`==` on purpose. `pytest.approx` would hide exactly the drift this test exists to catch: the
idf-coverage formula sums floats in a fixed sorted order, so equal inputs give bit-identical
outputs, and anything else is a real difference in what the gate sees.
"""

from __future__ import annotations

import dataclasses

import pytest

from atlas.config import SETTINGS
from atlas.retrieval import HybridIndex
from atlas.retrieval.rerank import Reranker
from atlas.text.tokenize import bigrams, tokenize

QUERIES = (
    "What is the minimum attendance to stay enrolled?",
    "How many grade points does the DD grade carry?",
    "What is the hostel curfew on weekdays?",
    "condonation for medical reasons",
    "percent",  # one token: the degenerate case for bigram coverage
)


@pytest.mark.parametrize("query", QUERIES)
def test_cached_coverage_equals_the_tokenising_path(mini_index, query):
    """Every chunk, every query: the two coverage paths agree to the last bit."""
    view = mini_index.view("campus")
    assert view is not None
    engine = Reranker(view.idf, settings=SETTINGS)
    tokens, pairs = tokenize(query), bigrams(tokenize(query))
    for position, chunk in enumerate(view._chunks):
        cached = engine.coverage_of_tokens(
            tokens, pairs, view._tokens[position], view._pairs[position]
        )
        recomputed = engine.coverage(query, chunk.text)
        assert cached == recomputed, (
            f"coverage drifted for {chunk.chunk_id!r} on {query!r}: "
            f"{cached!r} != {recomputed!r}"
        )


def test_the_two_arms_produce_identical_gate_scores(mini_store):
    """End to end: naive and fast settings must yield the same confidence, refusal and text."""
    naive_settings = dataclasses.replace(SETTINGS, perf_naive=True)
    fast_settings = dataclasses.replace(SETTINGS, perf_naive=False)
    naive = HybridIndex(mini_store, settings=naive_settings)
    fast = HybridIndex(mini_store, settings=fast_settings)
    for query in QUERIES:
        for mode in ("sparse", "dense", "hybrid"):
            old = naive.diagnostics(query, tenant_id="campus", mode=mode)
            new = fast.diagnostics(query, tenant_id="campus", mode=mode)
            # sparse mode legitimately returns nothing for a query whose terms are absent;
            # the two arms must still agree on *that*, which is itself a gate decision.
            assert (old is None) == (new is None), f"one arm found a passage on {query!r} ({mode})"
            if old is None:
                continue
            assert old[0].chunk_id == new[0].chunk_id, f"ranking moved on {query!r} ({mode})"
            for key in ("cosine", "coverage", "confidence"):
                assert old[1][key] == new[1][key], f"{key} moved on {query!r} ({mode})"


def test_the_cached_path_is_the_default(mini_store):
    """A clone must not silently run the slow arm: `perf_naive` is off unless asked for."""
    assert SETTINGS.perf_naive is False
