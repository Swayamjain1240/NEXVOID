"""A content fingerprint of one tenant's view, for the M25 cache key.

The cache must not serve an answer built from documents that have since changed. Hashing the
tenant's chunks - id, revision window and text - turns "the corpus changed" into a different
key, so invalidation needs no bookkeeping of its own: the old keys simply stop matching.

It is computed once, when the view is built, because a cache hit only happens when the view is
already in memory; hashing 34 chunks on every request would be work the cache exists to avoid.
"""

from __future__ import annotations

import hashlib

from ..store import StoredChunk


def corpus_fingerprint(chunks: list[StoredChunk]) -> str:
    """Stable 16-hex-digit hash over one tenant's chunk ids, windows and text."""
    digest = hashlib.blake2b(digest_size=16)
    for chunk in chunks:
        digest.update(
            f"{chunk.chunk_id}\0{chunk.valid_from}\0{chunk.valid_to}\0".encode()
        )
        digest.update(chunk.text.encode("utf-8"))
    return digest.hexdigest()[:16]
