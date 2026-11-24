"""Ranked chunk indices -> citable passages (M22 extraction).

A pure shape change with no policy in it, kept beside the view rather than inside it so the
view stays about ordering and the file ceiling stays honest.
"""

from __future__ import annotations

from ..interfaces import Passage
from ..store import StoredChunk


def to_passages(chunks: list[StoredChunk], ranked: list[tuple[int, float]]) -> list[Passage]:
    """(idx, score) pairs arrive score-descending; rank is the position in the output."""
    out: list[Passage] = []
    for rank, (idx, score) in enumerate(ranked):
        chunk = chunks[idx]
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
