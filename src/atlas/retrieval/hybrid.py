"""HybridIndex: one tenant's in-memory view over the store, exposing the Retriever seam.

(ATL-SwDD-001 s.6-s.7.) The view is built lazily per tenant and invalidated coarsely
(whole tenant) on ingest - fine-grained invalidation is a stage-2 optimisation with a
measurable target, not an M2 habit.

M21 (FR-29): every revision of a tenant's documents stays in the view, and `as_of` selects
the ones in force. The date is a filter *inside both retrievers*, never a post-filter on the
fused list, so a revision that was not in force cannot vote at all.
"""

from __future__ import annotations

from ..config import SETTINGS, Settings
from ..interfaces import InputError, Passage
from ..store import Store
from ..versions import parse_as_of
from .view import TenantView

# M22: the stages inside one retrieval, so a latency claim has an address instead of a
# single `retrieve_ms` that covers guard, BM25, vectors, fusion, ranking and the gate.
STAGES = ("embed_ms", "bm25_ms", "dense_ms", "fuse_ms", "rank_ms", "cosine_ms", "coverage_ms")

MODES = ("sparse", "dense", "hybrid")


class HybridIndex:
    """search(query, *, tenant_id, k, mode, as_of) -> ranked passages (FR-07, FR-29)."""

    def __init__(self, store: Store, *, settings: Settings = SETTINGS) -> None:
        self._store = store
        self._settings = settings
        self._views: dict[str, TenantView] = {}

    def invalidate(self, tenant_id: str) -> None:
        """Drop one tenant's view; the next search rebuilds it from the store."""
        self._views.pop(tenant_id, None)

    def corpus_hash(self, tenant_id: str) -> str:
        """Content fingerprint of this tenant's view, for the M25 cache key.

        Building the view is the 44 ms cost; a hit only happens when it is already warm, so
        this is a dictionary lookup in the case that matters. An empty tenant hashes to
        "empty", which still keys on the tenant itself.
        """
        view = self._view(tenant_id)
        return view.corpus_hash if view is not None else "empty"

    def search(
        self, query: str, *, tenant_id: str, k: int, mode: str = "hybrid", as_of: str = ""
    ) -> list[Passage]:
        """Contract: k >= 1, len(result) <= k, one tenant, score-descending order (FR-08, FR-09).

        A tenant with an empty view returns [] rather than raising: "no documents" is a
        legitimate state the M3 refusal gate decides about. With `as_of`, an empty result can
        also mean "nothing was in force on that date", which the answer path names (FR-30).
        """
        query = self._validate(query, tenant_id=tenant_id, k=k, mode=mode)
        as_of = parse_as_of(as_of)
        view = self._view(tenant_id)
        if view is None:
            return []
        return view.search(query, k=k, mode=mode, allowed=view.allowed(as_of))

    def _validate(self, query: str, *, tenant_id: str, k: int, mode: str) -> str:
        """The input contract, in one place so `diagnostics` can check before doing work."""
        if not tenant_id:
            raise InputError("empty tenant_id")
        if k < 1:
            raise InputError("k must be at least 1")
        if mode not in MODES:
            raise InputError(f"mode must be one of {MODES}, not {mode!r}")
        query = query.strip()
        if not query:
            raise InputError("empty query")
        return query

    def diagnostics(
        self,
        query: str,
        *,
        tenant_id: str,
        mode: str = "hybrid",
        as_of: str = "",
        timings: dict[str, float] | None = None,
    ) -> tuple[Passage, dict] | None:
        """Top passage plus its cosine / coverage / confidence split (ATL-TDD-001 s.7).

        Used by the eval harness for the refusal decision and by the answer path; returns
        None when the tenant view is empty, which the harness counts as a refusal at every
        theta. `timings` (M22) is filled with the sub-stages of this one retrieval; the query
        is embedded once here and the vector is shared with the gate.
        """
        query = self._validate(query, tenant_id=tenant_id, k=1, mode=mode)
        view = self._view(tenant_id)
        if view is None:
            return None
        query_vec = view.embed_query(query, timings=timings)
        passages = view.search(
            query,
            k=1,
            mode=mode,
            allowed=view.allowed(parse_as_of(as_of)),
            query_vec=query_vec,
            timings=timings,
        )
        if not passages:
            return None
        return passages[0], view.diagnose(
            query, passages[0], query_vec=query_vec, timings=timings
        )

    def candidate_set(
        self, query: str, *, tenant_id: str, as_of: str = ""
    ) -> tuple[list[tuple[int, float, str, str, str]], dict[str, dict[int, float]]]:
        """Public access to the reranker's inputs, for training and diagnosis (M8)."""
        view = self._view(tenant_id)
        if view is None:
            return [], {}
        return view.candidate_set(query, allowed=view.allowed(parse_as_of(as_of)))

    def view(self, tenant_id: str) -> TenantView | None:
        """Read-only view access: the trainer needs chunk labels, not SQL."""
        return self._view(tenant_id)

    def _view(self, tenant_id: str) -> TenantView | None:
        view = self._views.get(tenant_id)
        if view is None:
            chunks = self._store.list_chunks(tenant_id=tenant_id)
            if not chunks:
                return None
            view = TenantView(chunks, settings=self._settings)
            self._views[tenant_id] = view
        return view
