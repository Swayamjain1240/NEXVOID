"""The confidence gate's arithmetic (ATL-TDD-001 s.7), with the M22 cache.

Split out of the tenant view so ranking and gate scoring each have one home, and so the
report can time `cosine_ms` and `coverage_ms` separately instead of guessing which half of
the gate is expensive.

`score` answers the same question the view used to: how well does this passage match the
question, as `0.65·cosine + 0.35·idf_coverage`? What changed in M22 is where the coverage
*inputs* come from - the token sets the view already caches, not a fresh tokenisation of
the winning passage on every request. `perf_naive` keeps the old path alive as an ablation
arm, and only `bench/perf.py` selects it.
"""

from __future__ import annotations

import numpy as np

from ..config import Settings
from ..interfaces import Passage
from ..text.tokenize import bigrams, tokenize
from .rerank import Reranker
from .timing import stamp, start


class Confidence:
    """Cosine plus idf-coverage for one passage, from cached chunk sets (M22)."""

    def __init__(
        self,
        *,
        idf: dict[str, float],
        matrix: np.ndarray,
        embedder,
        index_of: dict[str, int],
        tokens: list[frozenset[str]],
        pairs: list[frozenset[str]],
        settings: Settings,
    ) -> None:
        self._matrix = matrix
        self._embedder = embedder
        self._index_of = index_of
        self._tokens = tokens
        self._pairs = pairs
        self._settings = settings
        self._coverage_of = Reranker(idf, settings=settings)

    def embed_query(
        self, query: str, *, timings: dict[str, float] | None = None
    ) -> np.ndarray:
        """The query vector, computed once per request so the gate need not re-embed it."""
        started = start()
        vector = self._embedder.embed([query])[0]
        stamp(timings, "embed_ms", started)
        return vector

    def score(
        self,
        query: str,
        passage: Passage,
        *,
        query_vec: np.ndarray | None = None,
        timings: dict[str, float] | None = None,
    ) -> dict[str, float]:
        """{cosine, coverage, confidence} for one retrieved passage."""
        position = self._index_of[passage.chunk_id]
        # the naive arm is the pre-M22 behaviour, kept only so bench/perf.py can measure it
        if query_vec is None or self._settings.perf_naive:
            query_vec = self.embed_query(query, timings=timings)
        started = start()
        cosine = float(np.dot(self._matrix[position], query_vec))
        stamp(timings, "cosine_ms", started)
        started = start()
        if self._settings.perf_naive:
            coverage = self._coverage_of.coverage(query, passage.text)
        else:
            tokens = tokenize(query)
            coverage = self._coverage_of.coverage_of_tokens(
                tokens, bigrams(tokens), self._tokens[position], self._pairs[position]
            )
        stamp(timings, "coverage_ms", started)
        confidence = (
            self._settings.confidence_cosine_weight * max(0.0, cosine)
            + self._settings.confidence_coverage_weight * coverage
        )
        return {"cosine": cosine, "coverage": coverage, "confidence": confidence}
