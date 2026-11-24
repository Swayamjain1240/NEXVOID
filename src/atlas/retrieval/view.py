"""One tenant's in-memory retrieval view: BM25, vectors, reranker (ATL-SwDD-001 s.6-s.7).

Split out of `hybrid.py` in M21 so the file that owns the public index can grow the
`as_of` parameter without crossing the 200-line ceiling, and so the version mask has one
home: `allowed()` is computed here and applied *inside* each retriever, which means fusion
never sees a chunk that was not in force on the date asked about.
"""

from __future__ import annotations

import numpy as np

from ..config import Settings
from ..interfaces import Passage
from ..store import StoredChunk
from ..text.tokenize import bigrams, tokenize
from ..versions import in_force
from .embedder import build_embedder
from .fuse import rrf
from .learned import build_reranker
from .sparse import BM25Index

# None means "every chunk": the caller did not ask about a date.
Allowed = frozenset[int] | None


class TenantView:
    """Built once per tenant from the store; every version is kept, not just the current one."""

    def __init__(self, chunks: list[StoredChunk], *, settings: Settings) -> None:
        self._settings = settings
        texts = [chunk.text for chunk in chunks]
        self._chunks = list(chunks)
        self._index_of = {chunk.chunk_id: idx for idx, chunk in enumerate(self._chunks)}
        self._sparse = BM25Index(texts, settings=settings)
        self._embedder = build_embedder(settings.embedder_kind, self._sparse.idf, settings=settings)
        self._matrix = self._embedder.embed(texts)
        self._reranker = build_reranker(self._sparse.idf, settings=settings)
        # The idf keys ARE the seen vocabulary: BM25 only stores terms some chunk contains (M14).
        self.vocabulary = frozenset(self._sparse.idf)
        # M21: the validity window per chunk, and the token set used to scope the novelty
        # signal to the versions in force (a 2026 word must not make a 2024 answer look known).
        self._windows = [(chunk.valid_from, chunk.valid_to) for chunk in self._chunks]
        self._tokens: list[frozenset[str]] = []
        self._allowed: dict[str, frozenset[int]] = {}
        self._scoped: dict[str, frozenset[str]] = {}
        # M5 perf: chunk features are immutable per view, so cache them once and the
        # reranker stops re-tokenising candidate text on every search (load-test finding)
        features: dict[int, tuple] = {}
        for idx, chunk in enumerate(self._chunks):
            ordered = tokenize(chunk.text)
            tokens = set(ordered)
            self._tokens.append(frozenset(tokens))
            features[idx] = (
                tokens,
                # bigrams of a *set* are hash-salted per process, which made identical runs
                # rank differently (M10 reproducibility check); order them from the tokens.
                set(bigrams(ordered)),
                set(tokenize(chunk.title)),
                set(tokenize(chunk.section)),
            )
        self._reranker.attach_features(features)

    def allowed(self, as_of: str) -> Allowed:
        """Indices in force on `as_of`; None when no date was asked about (FR-29)."""
        if not as_of:
            return None
        cached = self._allowed.get(as_of)
        if cached is None:
            cached = frozenset(
                idx
                for idx, (start, end) in enumerate(self._windows)
                if in_force(start, end, as_of)
            )
            self._allowed[as_of] = cached
        return cached

    def vocabulary_for(self, as_of: str) -> frozenset[str]:
        """Vocabulary of the revisions in force, so novelty is judged against that date."""
        allowed = self.allowed(as_of)
        if allowed is None:
            return self.vocabulary
        cached = self._scoped.get(as_of)
        if cached is None:
            cached = frozenset().union(*(self._tokens[idx] for idx in sorted(allowed)))
            self._scoped[as_of] = cached
        return cached

    def search(
        self, query: str, *, k: int, mode: str, depth: int | None = None, allowed: Allowed = None
    ) -> list[Passage]:
        s = self._settings
        depth = s.fusion_depth if depth is None else depth
        if mode == "sparse":
            return self._passages(self._sparse.search(tokenize(query), k=k, allowed=allowed)[:k])
        if mode == "dense":
            return self._passages(self._dense_search(query, k=k, allowed=allowed)[:k])
        candidates, context = self.candidate_set(query, depth=depth, allowed=allowed)
        reranked = self._reranker.rerank(
            query, candidates, k=min(k, s.rerank_candidates), context=context
        )
        return self._passages(reranked)

    def candidate_set(
        self, query: str, *, depth: int | None = None, allowed: Allowed = None
    ) -> tuple[list[tuple[int, float, str, str, str]], dict[str, dict[int, float]]]:
        """What the reranker sees: fused candidates plus their score context (shared with the
        M8 trainer on purpose - training on another pipeline is how a ranker stops working).
        `depth` truncates each retriever's list before fusion (M16); `allowed` filters inside
        both retrievers, so a version out of force cannot even vote (M21).
        """
        s = self._settings
        sparse_pool = self._sparse.search(tokenize(query), k=s.sparse_candidates, allowed=allowed)
        dense_pool = self._dense_search(query, k=s.dense_candidates, allowed=allowed)
        if depth:
            sparse_pool, dense_pool = sparse_pool[:depth], dense_pool[:depth]
        lists = [[idx for idx, _ in sparse_pool], [idx for idx, _ in dense_pool]]
        fused = rrf(lists, k=s.rrf_k, weights=(s.rrf_weight_sparse, s.rrf_weight_dense))
        fused_top = sorted(fused.items(), key=lambda item: (-item[1], item[0]))
        candidates: list[tuple[int, float, str, str, str]] = []
        for idx, score in fused_top[: s.fused_candidates]:
            chunk = self._chunks[idx]
            candidates.append((idx, score, chunk.text, chunk.title, chunk.section))
        return candidates, {"sparse": dict(sparse_pool), "cosine": dict(dense_pool)}

    @property
    def idf(self) -> dict[str, float]:
        """Corpus idf of this view; the trainer needs it to build the same features."""
        return self._sparse.idf

    def doc_of(self, idx: int) -> str:
        """Document id of a candidate index, for labelling training pairs (M8)."""
        return self._chunks[idx].doc_id

    def diagnose(self, query: str, passage: Passage) -> dict[str, float]:
        """Confidence components of one retrieved passage, formulas from ATL-TDD-001 s.7."""
        position = self._index_of[passage.chunk_id]
        query_vector = self._embedder.embed([query])[0]
        cosine = float(np.dot(self._matrix[position], query_vector))
        coverage = self._reranker.coverage(query, passage.text)
        confidence = (
            self._settings.confidence_cosine_weight * max(0.0, cosine)
            + self._settings.confidence_coverage_weight * coverage
        )
        return {"cosine": cosine, "coverage": coverage, "confidence": confidence}

    def _dense_search(
        self, query: str, *, k: int, allowed: Allowed = None
    ) -> list[tuple[int, float]]:
        """Cosine via the prebuilt unit-norm matrix; zero-score chunks are not candidates."""
        query_vec = self._embedder.embed([query])[0]
        scores = (self._matrix @ query_vec).tolist()
        candidates = [
            (idx, score)
            for idx, score in enumerate(scores)
            if score > 0.0 and (allowed is None or idx in allowed)
        ]
        return sorted(candidates, key=lambda item: (-item[1], item[0]))[:k]

    def _passages(self, ranked: list[tuple[int, float]]) -> list[Passage]:
        """(idx, score) pairs arrive score-descending; rank is the position in the output."""
        out: list[Passage] = []
        for rank, (idx, score) in enumerate(ranked):
            chunk = self._chunks[idx]
            out.append(
                Passage(
                    chunk_id=chunk.chunk_id,
                    doc_id=chunk.doc_id,
                    title=chunk.title,
                    section=chunk.section,
                    text=chunk.text,
                    score=score,
                    rank=rank,
                    valid_from=chunk.valid_from,
                    valid_to=chunk.valid_to,
                )
            )
        return out
