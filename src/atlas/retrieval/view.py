"""One tenant's in-memory retrieval view: BM25, vectors, reranker (ATL-SwDD-001 s.6-s.7).

Split out of `hybrid.py` in M21 so the file that owns the public index can carry the
`as_of` parameter without crossing the 200-line ceiling. M22 adds two things and removes
one: stage timings written into a request-owned dict, a coverage computation that reads
the cached token sets the reranker already holds, and the second query embedding.
"""

from __future__ import annotations

import numpy as np

from ..config import Settings
from ..interfaces import Passage
from ..store import StoredChunk
from ..text.tokenize import bigrams, tokenize
from .confidence import Confidence
from .embedder import build_embedder
from .fingerprint import corpus_fingerprint
from .fuse import rrf
from .learned import build_reranker
from .passages import to_passages
from .sparse import BM25Index
from .timing import stamp, start
from .windows import WindowMask

# None means "every chunk": the caller did not ask about a date.
Allowed = frozenset[int] | None


class TenantView:
    """Built once per tenant from the store; every version is kept, not just the current one."""

    def __init__(self, chunks: list[StoredChunk], *, settings: Settings) -> None:
        self._settings = settings
        texts = [chunk.text for chunk in chunks]
        self._chunks = list(chunks)
        self.corpus_hash = corpus_fingerprint(self._chunks)  # M25: the cache key's corpus part
        self._index_of = {chunk.chunk_id: idx for idx, chunk in enumerate(self._chunks)}
        self._sparse = BM25Index(texts, settings=settings)
        self._embedder = build_embedder(settings.embedder_kind, self._sparse.idf, settings=settings)
        self._matrix = self._embedder.embed(texts)
        self._reranker = build_reranker(self._sparse.idf, settings=settings)
        self._tokens: list[frozenset[str]] = []
        self._pairs: list[frozenset[str]] = []
        features: dict[int, tuple] = {}
        for idx, chunk in enumerate(self._chunks):
            ordered = tokenize(chunk.text)
            tokens, pairs = set(ordered), set(bigrams(ordered))
            self._tokens.append(frozenset(tokens))
            self._pairs.append(frozenset(pairs))
            # bigrams of a *set* are hash-salted per process, which made identical runs rank
            # differently (M10 reproducibility check); order them from the tokens.
            features[idx] = (
                tokens,
                pairs,
                set(tokenize(chunk.title)),
                set(tokenize(chunk.section)),
            )
        self._reranker.attach_features(features)
        self.mask = WindowMask(self._chunks, self._tokens)
        # M22: the gate reads these caches instead of re-tokenising the winning passage,
        # which was 71% of query CPU until it was measured (results/perf-m22.md).
        self.confidence = Confidence(
            idf=self._sparse.idf,
            matrix=self._matrix,
            embedder=self._embedder,
            index_of=self._index_of,
            tokens=self._tokens,
            pairs=self._pairs,
            settings=settings,
        )

    @property
    def vocabulary(self) -> frozenset[str]:
        """Every term this tenant's corpus carries, across all revisions."""
        return self.mask.vocabulary

    def allowed(self, as_of: str) -> Allowed:
        """Indices in force on `as_of`; None when no date was asked about (FR-29)."""
        return self.mask.allowed(as_of)

    def vocabulary_for(self, as_of: str) -> frozenset[str]:
        """Vocabulary of the revisions in force, so novelty is judged against that date."""
        return self.mask.vocabulary_for(as_of)

    def embed_query(self, query: str, *, timings: dict[str, float] | None = None) -> np.ndarray:
        """The query vector, computed once per request so the gate need not re-embed it."""
        return self.confidence.embed_query(query, timings=timings)

    def search(
        self,
        query: str,
        *,
        k: int,
        mode: str,
        depth: int | None = None,
        allowed: Allowed = None,
        query_vec: np.ndarray | None = None,
        timings: dict[str, float] | None = None,
    ) -> list[Passage]:
        s = self._settings
        depth = s.fusion_depth if depth is None else depth
        if mode == "sparse":
            started = start()
            ranked = self._sparse.search(tokenize(query), k=k, allowed=allowed)[:k]
            stamp(timings, "bm25_ms", started)
            return to_passages(self._chunks, ranked)
        if mode == "dense":
            dense = self._dense_search(
                query, k=k, allowed=allowed, query_vec=query_vec, timings=timings
            )
            return to_passages(self._chunks, dense[:k])
        candidates, context = self.candidate_set(
            query, depth=depth, allowed=allowed, query_vec=query_vec, timings=timings
        )
        started = start()
        reranked = self._reranker.rerank(
            query, candidates, k=min(k, s.rerank_candidates), context=context
        )
        stamp(timings, "rank_ms", started)
        return to_passages(self._chunks, reranked)

    def candidate_set(
        self,
        query: str,
        *,
        depth: int | None = None,
        allowed: Allowed = None,
        query_vec: np.ndarray | None = None,
        timings: dict[str, float] | None = None,
    ) -> tuple[list[tuple[int, float, str, str, str]], dict[str, dict[int, float]]]:
        """What the reranker sees: fused candidates plus their score context (shared with the
        M8 trainer on purpose - training on another pipeline is how a ranker stops working).
        `depth` truncates each retriever's list before fusion (M16); `allowed` filters inside
        both retrievers, so a version out of force cannot even vote (M21).
        """
        s = self._settings
        started = start()
        sparse_pool = self._sparse.search(tokenize(query), k=s.sparse_candidates, allowed=allowed)
        stamp(timings, "bm25_ms", started)
        dense_pool = self._dense_search(
            query, k=s.dense_candidates, allowed=allowed, query_vec=query_vec, timings=timings
        )
        if depth:
            sparse_pool, dense_pool = sparse_pool[:depth], dense_pool[:depth]
        started = start()
        lists = [[idx for idx, _ in sparse_pool], [idx for idx, _ in dense_pool]]
        fused = rrf(lists, k=s.rrf_k, weights=(s.rrf_weight_sparse, s.rrf_weight_dense))
        fused_top = sorted(fused.items(), key=lambda item: (-item[1], item[0]))
        candidates: list[tuple[int, float, str, str, str]] = []
        for idx, score in fused_top[: s.fused_candidates]:
            chunk = self._chunks[idx]
            candidates.append((idx, score, chunk.text, chunk.title, chunk.section))
        stamp(timings, "fuse_ms", started)
        return candidates, {"sparse": dict(sparse_pool), "cosine": dict(dense_pool)}

    @property
    def idf(self) -> dict[str, float]:
        """Corpus idf of this view; the trainer needs it to build the same features."""
        return self._sparse.idf

    def doc_of(self, idx: int) -> str:
        """Document id of a candidate index, for labelling training pairs (M8)."""
        return self._chunks[idx].doc_id

    def diagnose(
        self,
        query: str,
        passage: Passage,
        *,
        query_vec: np.ndarray | None = None,
        timings: dict[str, float] | None = None,
    ) -> dict[str, float]:
        """Confidence components of one retrieved passage, formulas from ATL-TDD-001 s.7."""
        return self.confidence.score(query, passage, query_vec=query_vec, timings=timings)

    def _dense_search(
        self,
        query: str,
        *,
        k: int,
        allowed: Allowed = None,
        query_vec: np.ndarray | None = None,
        timings: dict[str, float] | None = None,
    ) -> list[tuple[int, float]]:
        """Cosine via the prebuilt unit-norm matrix; zero-score chunks are not candidates."""
        if query_vec is None:
            query_vec = self.embed_query(query, timings=timings)
        started = start()
        scores = (self._matrix @ query_vec).tolist()
        candidates = [
            (idx, score)
            for idx, score in enumerate(scores)
            if score > 0.0 and (allowed is None or idx in allowed)
        ]
        ranked = sorted(candidates, key=lambda item: (-item[1], item[0]))[:k]
        stamp(timings, "dense_ms", started)
        return ranked
