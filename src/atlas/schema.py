"""The SQLite schema and its in-place migrations (ATL-SwDD-001 s.2, M21).

Kept beside `store.py` rather than inside it so the store stays about behaviour - what a row
means and when a revision is in force - and the DDL lives in one place a reader can diff
against an older database.

M21 adds `valid_from` and `valid_to` to `chunks`. A database written before them is migrated
in place: both default to `''`, which `versions.in_force` reads as "unbounded", and that is
exactly what those rows were - documents with no recorded effective date.
"""

from __future__ import annotations

import sqlite3

SCHEMA = """
CREATE TABLE IF NOT EXISTS documents (
    tenant_id    TEXT    NOT NULL,
    doc_id       TEXT    NOT NULL,
    title        TEXT    NOT NULL,
    content_hash TEXT    NOT NULL,
    PRIMARY KEY (tenant_id, title)
);
CREATE TABLE IF NOT EXISTS chunks (
    tenant_id  TEXT NOT NULL,
    chunk_id   TEXT NOT NULL,
    doc_id     TEXT NOT NULL,
    seq        INTEGER NOT NULL,
    title      TEXT NOT NULL,
    section    TEXT NOT NULL,
    text       TEXT NOT NULL,
    valid_from TEXT NOT NULL DEFAULT '',
    valid_to   TEXT NOT NULL DEFAULT '',
    PRIMARY KEY (tenant_id, chunk_id)
);
CREATE INDEX IF NOT EXISTS idx_chunks_tenant ON chunks (tenant_id);
CREATE INDEX IF NOT EXISTS idx_chunks_doc ON chunks (tenant_id, doc_id);
-- M30: a corpus version per tenant, moved by the write itself rather than recomputed by the
-- reader. Triggers rather than a call in `Store.upsert_document`, so a writer that bypasses
-- this package - a bulk loader, a migration script, a person with sqlite3 - still moves it.
CREATE TABLE IF NOT EXISTS corpus_version (
    tenant_id TEXT    PRIMARY KEY,
    version   INTEGER NOT NULL DEFAULT 0
);
CREATE TRIGGER IF NOT EXISTS corpus_version_insert AFTER INSERT ON documents
BEGIN
    INSERT INTO corpus_version (tenant_id, version) VALUES (NEW.tenant_id, 1)
    ON CONFLICT(tenant_id) DO UPDATE SET version = version + 1;
END;
CREATE TRIGGER IF NOT EXISTS corpus_version_update AFTER UPDATE ON documents
BEGIN
    INSERT INTO corpus_version (tenant_id, version) VALUES (NEW.tenant_id, 1)
    ON CONFLICT(tenant_id) DO UPDATE SET version = version + 1;
END;
CREATE TRIGGER IF NOT EXISTS corpus_version_delete AFTER DELETE ON documents
BEGIN
    INSERT INTO corpus_version (tenant_id, version) VALUES (OLD.tenant_id, 1)
    ON CONFLICT(tenant_id) DO UPDATE SET version = version + 1;
END;
"""

# Applied only when the column is absent, so opening an old database twice is a no-op.
MIGRATIONS = (
    ("valid_from", "ALTER TABLE chunks ADD COLUMN valid_from TEXT NOT NULL DEFAULT ''"),
    ("valid_to", "ALTER TABLE chunks ADD COLUMN valid_to TEXT NOT NULL DEFAULT ''"),
)


def migrate(conn: sqlite3.Connection) -> None:
    """Add the window columns to a database created before M21 (idempotent)."""
    columns = {row[1] for row in conn.execute("PRAGMA table_info(chunks)")}
    for name, statement in MIGRATIONS:
        if name not in columns:
            conn.execute(statement)
