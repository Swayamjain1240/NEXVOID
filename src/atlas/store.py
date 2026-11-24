"""SQLite persistence, tenancy and revision windows (ATL-SwDD-001 s.2, M21).

The store owns rows, not ranking opinions: it hashes content with BLAKE2b (never Python's
salted hash()), and physically scopes every read by tenant_id (FR-01..FR-03, FR-06, FR-08).

M21 adds revision history without rewriting the past: a chunk carries the window it was in
force (`valid_from`, `valid_to`, ISO dates, `""` = unbounded). Ingesting a newer revision
*closes* the open one instead of deleting it, so a question can still be answered as of an
earlier date (FR-29), and a back-dated ingest is refused rather than guessed at.
"""

from __future__ import annotations

import hashlib
import sqlite3
from dataclasses import dataclass
from pathlib import Path

from .config import SETTINGS, Settings
from .interfaces import InputError, TenantError
from .schema import SCHEMA, migrate
from .text.chunk import chunk_document
from .versions import UNBOUNDED, parse_date, previous_day


def blake2b_hex(value: str, *, length: int = 12) -> str:
    """Stable content hash: identical runs produce identical ids (ATL-TDD-001 s.11)."""
    digest = hashlib.blake2b(value.encode("utf-8"), digest_size=16).hexdigest()
    return digest[:length]


@dataclass(frozen=True)
class StoredChunk:
    """A chunk as persisted: identity, provenance, the window it applies to, and its text."""

    chunk_id: str
    doc_id: str
    title: str
    section: str
    text: str
    seq: int
    valid_from: str = UNBOUNDED
    valid_to: str = UNBOUNDED


class Store:
    """The only module that touches SQLite; every method is tenant-scoped by hand."""

    def __init__(self, path: str | Path, *, settings: Settings = SETTINGS) -> None:
        self._settings = settings
        self._path = Path(path)
        self._path.parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(str(self._path), check_same_thread=False)
        self._conn.executescript(SCHEMA)
        migrate(self._conn)
        self._conn.commit()

    def close(self) -> None:
        self._conn.close()

    def upsert_document(
        self, *, tenant_id: str, path: Path, effective: str | None = None, doc_id: str | None = None
    ) -> int:
        """Ingest one markdown file at an effective date; unchanged content no-ops (FR-02, FR-03).

        `effective` empty means "no recorded effective date": the chunk is in force at every
        date. A dated ingest closes the version that was open the day before it, so history is
        appended rather than overwritten; ingesting a date older than an existing open version
        is an InputError, because a silent back-date would rewrite an answer already given.
        """
        if not tenant_id:
            raise TenantError("empty tenant_id")
        raw = path.read_text(encoding="utf-8")
        content_hash = blake2b_hex(raw, length=32)
        title, _ = _title_of(raw, path.stem)
        prior = self._conn.execute(
            "SELECT doc_id, content_hash FROM documents WHERE tenant_id = ? AND title = ?",
            (tenant_id, title),
        ).fetchone()
        if prior is not None and prior[1] == content_hash:
            return 0
        # the document id is the readable file stem: stable, and exactly what the frozen
        # golden set labels reference; chunk ids below stay BLAKE2b-derived
        doc_id = doc_id or (prior[0] if prior is not None else path.stem)
        start = parse_date(effective or "", field="effective")
        self._close_open(tenant_id=tenant_id, doc_id=doc_id, start=start)
        chunks = chunk_document(raw, settings=self._settings)
        for seq, chunk in enumerate(chunks):
            self._conn.execute(
                "INSERT OR REPLACE INTO chunks VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    tenant_id,
                    _chunk_id(tenant_id, doc_id, seq, start),
                    doc_id,
                    seq,
                    title,
                    chunk.section,
                    chunk.text,
                    start,
                    UNBOUNDED,
                ),
            )
        self._conn.execute(
            "INSERT OR REPLACE INTO documents VALUES (?, ?, ?, ?)",
            (tenant_id, doc_id, title, content_hash),
        )
        self._conn.commit()
        return len(chunks)

    def _close_open(self, *, tenant_id: str, doc_id: str, start: str) -> None:
        """End the version that was open before `start`; refuse a back-dated ingest."""
        rows = self._conn.execute(
            "SELECT valid_from FROM chunks WHERE tenant_id = ? AND doc_id = ? AND valid_to = ''",
            (tenant_id, doc_id),
        ).fetchall()
        later = sorted(row[0] for row in rows if row[0] > start)
        if later:
            raise InputError(
                f"cannot back-date {doc_id!r} to {start or 'no date'}: "
                f"a version effective {later[0]} is already open"
            )
        if not rows or not start:
            return
        self._conn.execute(
            "UPDATE chunks SET valid_to = ? WHERE tenant_id = ? AND doc_id = ? AND valid_to = ''",
            (previous_day(start), tenant_id, doc_id),
        )

    def list_chunks(self, *, tenant_id: str) -> list[StoredChunk]:
        """Every chunk of one tenant in stable order; other tenants are unreachable here."""
        if not tenant_id:
            raise TenantError("empty tenant_id")
        rows = self._conn.execute(
            "SELECT chunk_id, doc_id, title, section, text, seq, valid_from, valid_to FROM chunks "
            "WHERE tenant_id = ? ORDER BY seq, chunk_id",
            (tenant_id,),
        ).fetchall()
        return [StoredChunk(*row) for row in rows]

    def revisions(self, *, tenant_id: str, doc_id: str) -> list[tuple[str, str, int]]:
        """(valid_from, valid_to, chunks) for one document, oldest first - the history view."""
        rows = self._conn.execute(
            "SELECT valid_from, valid_to, COUNT(*) FROM chunks "
            "WHERE tenant_id = ? AND doc_id = ? GROUP BY valid_from, valid_to "
            "ORDER BY valid_from, valid_to",
            (tenant_id, doc_id),
        ).fetchall()
        return [(row[0], row[1], row[2]) for row in rows]

    def stats(self, *, tenant_id: str) -> dict[str, int]:
        """{documents: n, chunks: n}; the ingest acceptance check (FR-01) reads this."""
        if not tenant_id:
            raise TenantError("empty tenant_id")
        documents = self._conn.execute(
            "SELECT COUNT(*) FROM documents WHERE tenant_id = ?", (tenant_id,)
        ).fetchone()[0]
        chunks = self._conn.execute(
            "SELECT COUNT(*) FROM chunks WHERE tenant_id = ?", (tenant_id,)
        ).fetchone()[0]
        return {"documents": documents, "chunks": chunks}


def _chunk_id(tenant_id: str, doc_id: str, seq: int, valid_from: str = UNBOUNDED) -> str:
    """Id is derived from immutable identity, so a rebuild cannot renumber a citation.

    The effective date is part of that identity from M21: two revisions of one document must
    not share a chunk id, or a citation to the 2025 rule would resolve to the 2026 text.
    """
    return blake2b_hex(f"{tenant_id}\0{doc_id}\0{seq}\0{valid_from}", length=16)


def _title_of(raw: str, fallback: str) -> tuple[str, str]:
    """Title = first H1 heading, else the file stem with dashes for spaces."""
    for line in raw.splitlines():
        stripped = line.strip()
        if stripped.startswith("# "):
            return stripped[2:].strip(), fallback
        if stripped.startswith("#"):
            return stripped.lstrip("#").strip(), fallback
    return fallback.replace("-", " ").replace("_", " ").strip(), fallback
