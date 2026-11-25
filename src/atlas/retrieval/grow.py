"""Append-only growth for a tenant view (M26).

`TenantView.__init__` builds BM25, the vector matrix, the token caches and the ranker features
from every chunk. When a document is ingested only the new chunks are new, so this module
appends those and reuses everything already computed: an ingest costs O(new chunks) plus
BM25's idf sweep over the vocabulary, instead of O(corpus).

The limit is stated rather than hidden. Existing vectors keep the idf they were built with,
because hashed TF-IDF bakes corpus idf into the row, and appending a document moves every idf
a little. `results/scale.md` measures whether that changes a ranking, and L21 records the gap.
Removal or a revision window change is not patched here at all - `HybridIndex.refresh` rebuilds
in that case, because a window is not something a posting list can forget.
"""

from __future__ import annotations

import numpy as np

from ..store import StoredChunk
from ..text.tokenize import bigrams, tokenize
from .confidence import Confidence
from .fingerprint import corpus_fingerprint
from .windows import WindowMask


def extend_view(view, new_chunks: list[StoredChunk]) -> None:
    """Append chunks to a built view in place; every existing row is kept as it was."""
    start = len(view._chunks)
    view._chunks.extend(new_chunks)
    for offset, chunk in enumerate(new_chunks):
        view._index_of[chunk.chunk_id] = start + offset
    texts = [chunk.text for chunk in new_chunks]
    view._sparse.append(texts)
    view._matrix = np.vstack([view._matrix, view._embedder.embed(texts)])
    for offset, chunk in enumerate(new_chunks):
        position = start + offset
        ordered = tokenize(chunk.text)
        tokens, pairs = set(ordered), set(bigrams(ordered))
        view._tokens.append(frozenset(tokens))
        view._pairs.append(frozenset(pairs))
        view._features[position] = (
            tokens,
            pairs,
            set(tokenize(chunk.title)),
            set(tokenize(chunk.section)),
        )
    view._reranker.attach_features(view._features)
    # the cache key carries the corpus fingerprint, so growth must move it or an answer
    # computed before the ingest would keep being served after it
    view.corpus_hash = corpus_fingerprint(view._chunks)
    # the date filter and the gate hold the same lists, so both are rebuilt over the new
    # length; that is O(chunks) over tuples, not the O(chunks) tokenisation this avoids
    view.mask = WindowMask(view._chunks, view._tokens)
    view.confidence = Confidence(
        idf=view._sparse.idf,
        matrix=view._matrix,
        embedder=view._embedder,
        index_of=view._index_of,
        tokens=view._tokens,
        pairs=view._pairs,
        settings=view._settings,
    )
