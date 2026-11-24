"""Which chunks were in force on a date, and what vocabulary they carry (M21, M22).

Split out of the tenant view so "the date filter" has one owner and the ranking module
stays about ranking. Both answers are memoised per date: the corpus is immutable for the
life of a view, so the same date always produces the same set.

`None` from `allowed()` means "no date was asked about" - every revision, which is the
pre-M21 behaviour and must stay the default.
"""

from __future__ import annotations

from ..store import StoredChunk
from ..versions import in_force


class WindowMask:
    """Per-date membership and vocabulary for one tenant's revisions."""

    def __init__(self, chunks: list[StoredChunk], tokens: list[frozenset[str]]) -> None:
        self._windows = [(chunk.valid_from, chunk.valid_to) for chunk in chunks]
        self._tokens = tokens
        self._allowed: dict[str, frozenset[int]] = {}
        self._scoped: dict[str, frozenset[str]] = {}
        # The tenant's whole vocabulary: the novelty signal's "does the corpus know this word".
        self.vocabulary = frozenset().union(*tokens) if tokens else frozenset()

    def allowed(self, as_of: str) -> frozenset[int] | None:
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
