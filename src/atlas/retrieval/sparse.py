"""Sparse retrieval: BM25 over an inverted index (ATL-TDD-001 s.3).

Query cost is proportional to the postings of the query's terms, not to the
corpus; ties are broken by chunk index so equal scores order deterministically.
"""

from __future__ import annotations

import math

from ..config import SETTINGS, Settings
from ..text.tokenize import tokenize


class BM25Index:
    """Built once per tenant view; queried many times."""

    def __init__(self, chunk_texts: list[str], *, settings: Settings = SETTINGS) -> None:
        self._settings = settings
        token_docs = [tokenize(text) for text in chunk_texts]
        self._doc_len = [len(tokens) for tokens in token_docs]
        self._avgdl = sum(self._doc_len) / len(self._doc_len) if self._doc_len else 0.0
        self.idf: dict[str, float] = {}
        self._postings: dict[str, list[tuple[int, int]]] = {}
        for idx, tokens in enumerate(token_docs):
            for term, tf in _term_counts(tokens).items():
                self._postings.setdefault(term, []).append((idx, tf))
        n_docs = len(self._doc_len)
        for term, postings in self._postings.items():
            self.idf[term] = math.log((n_docs + 1) / (len(postings) + 0.5))

    def append(self, chunk_texts: list[str]) -> None:
        """Extend the index with new chunks, keeping every posting already built (M26).

        The sweep over `idf` is O(vocabulary), not O(chunks): idf is a function of document
        frequency and corpus size, both of which just moved, so every term's weight shifts a
        little and has to be recomputed. That is cheap next to re-tokenising the corpus, and
        it is the reason an ingest no longer costs the same as a cold start.
        """
        start = len(self._doc_len)
        token_docs = [tokenize(text) for text in chunk_texts]
        self._doc_len.extend(len(tokens) for tokens in token_docs)
        self._avgdl = sum(self._doc_len) / len(self._doc_len) if self._doc_len else 0.0
        for offset, tokens in enumerate(token_docs):
            for term, tf in _term_counts(tokens).items():
                self._postings.setdefault(term, []).append((start + offset, tf))
        n_docs = len(self._doc_len)
        # in place: the embedder and the rankers hold this dict, and a replacement would
        # leave them scoring with the idf of the previous corpus
        self.idf.clear()
        self.idf.update(
            (term, math.log((n_docs + 1) / (len(postings) + 0.5)))
            for term, postings in self._postings.items()
        )

    def search(
        self, query_terms: list[str], *, k: int, allowed: frozenset[int] | None = None
    ) -> list[tuple[int, float]]:
        """Top-k (chunk_index, score), score desc, ties by ascending chunk index (FR-09, FR-12).

        `allowed` (M21) restricts scoring to chunks in force on a date, so a superseded
        revision cannot occupy a slot that a later filter would then have to remove.
        """
        scores: dict[int, float] = {}
        for term, _qtf in _term_counts(query_terms).items():
            postings = self._postings.get(term)
            if not postings:
                continue
            idf = self.idf[term]
            k1, b, avgdl = self._settings.bm25_k1, self._settings.bm25_b, self._avgdl
            for idx, tf in postings:
                if allowed is not None and idx not in allowed:
                    continue
                denom = tf + k1 * (1 - b + b * self._doc_len[idx] / avgdl)
                scores[idx] = scores.get(idx, 0.0) + idf * (tf * (k1 + 1)) / denom
        ranked = sorted(scores.items(), key=lambda item: (-item[1], item[0]))
        return ranked[:k]


def _term_counts(tokens: list[str]) -> dict[str, int]:
    counts: dict[str, int] = {}
    for token in tokens:
        counts[token] = counts.get(token, 0) + 1
    return counts
