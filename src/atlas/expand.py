"""Corpus-derived query expansion (FR-17, M27): a query term's neighbours, from the corpus.

FR-17 is the last correctness gap and it is a vocabulary problem, not a latency one: a question
phrased with words the corpus uses differently is answered from the wrong passage or refused.
The literature says expansion helps *and* that it hurts, so this module is an experiment with an
off switch, not a shipped default.

The signal is co-occurrence inside the tenant's own chunks - the token sets `TenantView` already
holds for coverage - scored by pointwise mutual information:

    pmi(a, b) = log( (count(a, b) * chunks) / (df(a) * df(b)) )

A pair that appears together more often than their frequencies predict is a candidate; a pair that
merely shares a common word is not. No model, no key, no training corpus: the tenant's documents
are the only input, and two processes over the same corpus build the same table.

Two deliberate limits, both measured in `results/expansion.md`:

- Expansion touches the *retrieval* query only. The refusal gate keeps reading the question as
  asked, because a gate that scored an expanded query would be grading its own homework: adding
  corpus words would erase exactly the novelty FR-16 measures.
- The vocabulary is corpus-wide, not date-scoped, so an expanded query can see a word only a later
  revision carries. That is L22, and it is one reason the default threshold is 0.0 (off).
"""

from __future__ import annotations

import math
from collections import Counter
from collections.abc import Sequence

from .text.tokenize import tokenize

# Terms rarer than this cannot support a co-occurrence estimate: one chunk in common is noise.
MIN_DF = 2
# Ceiling on the table's size, so a large corpus costs a bounded build rather than a quadratic one.
MAX_TERMS = 4000


class TermExpander:
    """PMI over chunk-level co-occurrence, built once per view and cached on it."""

    def __init__(
        self,
        token_sets: Sequence[frozenset[str]],
        *,
        min_df: int = MIN_DF,
        max_terms: int = MAX_TERMS,
    ) -> None:
        self.chunks = len(token_sets)
        df: Counter[str] = Counter()
        for tokens in token_sets:
            df.update(tokens)
        keep = {term for term, count in df.items() if count >= min_df}
        if len(keep) > max_terms:
            keep = set(sorted(keep, key=lambda term: (-df[term], term))[:max_terms])
        self.df = {term: df[term] for term in sorted(keep)}
        adjacency: dict[str, Counter[str]] = {term: Counter() for term in self.df}
        for tokens in token_sets:
            kept = sorted(tokens & keep)
            for position, left in enumerate(kept):
                for right in kept[position + 1 :]:
                    adjacency[left][right] += 1
                    adjacency[right][left] += 1
        self._adjacency = adjacency

    def __len__(self) -> int:
        return len(self.df)

    def neighbours(self, term: str, *, threshold: float, limit: int) -> tuple[str, ...]:
        """Terms co-occurring with `term` above `threshold` PMI, strongest first, ties by term.

        Sorted explicitly rather than relying on dict order, so two processes and two runs over
        the same corpus return the same tuple - the determinism probe reads answers, not tables.
        """
        if term not in self._adjacency or limit <= 0 or self.chunks <= 0:
            return ()
        scored = []
        for other, count in self._adjacency[term].items():
            pmi = math.log((count * self.chunks) / (self.df[term] * self.df[other]))
            if pmi >= threshold:
                scored.append((-pmi, -count, other))
        scored.sort()
        return tuple(other for _, _, other in scored[:limit])


def expanded_query(
    query: str,
    expander: TermExpander,
    *,
    threshold: float,
    limit: int,
) -> str:
    """The query with corpus neighbours of its terms appended, or the query itself.

    Added terms are appended rather than weighted: BM25 and the embedder both read the query as
    a bag, so a weighted form would have to change both, and the point of the experiment is to
    measure the corpus signal, not a new query language.
    """
    terms = sorted(set(tokenize(query)))
    added: list[str] = []
    for term in terms:
        for other in expander.neighbours(term, threshold=threshold, limit=limit):
            if other not in terms and other not in added:
                added.append(other)
    if not added:
        return query
    return f"{query} {' '.join(sorted(added))}"


def query_for_retrieval(query: str, index: object, tenant_id: str, settings: object) -> str:
    """The query retrieval should see: the question, plus corpus neighbours when enabled.

    Kept here rather than in `answer.py` so the answer path stays one call and the 200-line
    ceiling is not the reason a feature was written twice. `index` and `settings` are duck-typed
    because importing them here would make `retrieval.view` and this module import each other.
    """
    if getattr(settings, "expansion_threshold", 0.0) <= 0.0:
        return query
    view = index.view(tenant_id)
    if view is None:
        return query
    return expanded_query(
        query,
        view.expander(),
        threshold=settings.expansion_threshold,
        limit=settings.expansion_terms,
    )


def added_terms(before: str, after: str) -> tuple[str, ...]:
    """The terms expansion appended, sorted - for the report's per-question detail."""
    return tuple(sorted(set(tokenize(after)) - set(tokenize(before))))


__all__ = [
    "MAX_TERMS",
    "MIN_DF",
    "TermExpander",
    "added_terms",
    "expanded_query",
    "query_for_retrieval",
]
