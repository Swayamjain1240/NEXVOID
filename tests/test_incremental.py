"""M26: growing a view without rebuilding it, and the cases that must rebuild.

The interesting failures are not "did the new chunk arrive" but "did the old ones survive":
a changed document or a closed revision window cannot be patched into a posting list, and a
view that appends anyway would answer with text the store no longer holds.
"""

from __future__ import annotations

import dataclasses
import json
import threading
import time
import urllib.parse
import urllib.request

from atlas.config import SETTINGS
from atlas.retrieval import HybridIndex
from atlas.server import ServerState, make_server
from atlas.store import Store
from tests.conftest import MINI_CORPUS

NEW_DOC = (
    "# Library Rules\n\n"
    "## Loans\n\n"
    "The library lends four books at a time for fourteen days. A loan can be renewed twice "
    "unless another reader has reserved the title. A lost book is charged at replacement cost.\n"
)
EXAM_DOC = (
    "# Examination Rules\n\n"
    "## Malpractice\n\n"
    "Copying in an examination is malpractice and leads to cancellation of the paper. The "
    "committee hears the case within thirty days of the invigilation report.\n"
)
REVISED_ATTENDANCE = (
    "# Attendance Policy\n\n"
    "## Condonation\n\n"
    "Students must keep eighty percent attendance for every course in a semester. Below the "
    "bar the student is detained and cannot sit the end-term examination.\n"
)


def _add(store, tmp_path, name, text, *, tenant="campus", effective=None, doc_id=None):
    path = tmp_path / name
    path.write_text(text, encoding="utf-8")
    return store.upsert_document(
        tenant_id=tenant, path=path, effective=effective, doc_id=doc_id
    )


def test_an_ingested_document_is_appended_not_rebuilt(mini_store, tmp_path):
    index = HybridIndex(mini_store)
    before = index.search("library loans", tenant_id="campus", k=3)
    # hashed vectors collide, so "no match" is not an empty list: assert on the document
    assert all(passage.doc_id != "library-rules" for passage in before)
    built = index.view("campus")
    _add(mini_store, tmp_path, "library-rules.md", NEW_DOC)
    assert index.refresh("campus") == "extend"
    assert index.view("campus") is built  # the same object: nothing was rebuilt
    hits = index.search("library loans", tenant_id="campus", k=3)
    assert hits and hits[0].doc_id == "library-rules"


def test_a_changed_document_rebuilds(mini_store, tmp_path):
    index = HybridIndex(mini_store)
    index.search("attendance", tenant_id="campus", k=1)
    _add(mini_store, tmp_path, "attendance-policy.md", REVISED_ATTENDANCE,
         doc_id="attendance-policy")
    assert index.refresh("campus") == "rebuild"
    hits = index.search("attendance every course", tenant_id="campus", k=1)
    assert "eighty percent" in hits[0].text


def test_a_closed_revision_window_rebuilds(mini_store, tmp_path):
    index = HybridIndex(mini_store)
    index.search("attendance", tenant_id="campus", k=1)
    built = index.view("campus")
    _add(mini_store, tmp_path, "attendance-policy@2025-01-01.md", REVISED_ATTENDANCE,
         effective="2025-01-01", doc_id="attendance-policy")
    assert index.refresh("campus") == "rebuild"
    assert index.view("campus") is not built


def test_refresh_is_a_no_op_when_nothing_was_ingested(mini_store):
    index = HybridIndex(mini_store)
    index.search("attendance", tenant_id="campus", k=1)
    assert index.refresh("campus") == "none"
    assert index.refresh("nobody") == "missing"


def test_incremental_growth_keeps_the_same_rankings(mini_store, tmp_path):
    """The idf drift of an append must not reorder a result; `results/scale.md` says why."""
    index = HybridIndex(mini_store)
    index.search("attendance", tenant_id="campus", k=1)  # build the view first
    _add(mini_store, tmp_path, "library-rules.md", NEW_DOC)
    _add(mini_store, tmp_path, "exam-rules.md", EXAM_DOC)
    assert index.refresh("campus") == "extend"
    query = "attendance for every course"
    grown = index.search(query, tenant_id="campus", k=5)
    fresh = HybridIndex(mini_store).search(query, tenant_id="campus", k=5)
    assert [passage.chunk_id for passage in grown] == [passage.chunk_id for passage in fresh]


def test_the_store_runs_in_wal_mode(tmp_path):
    store = Store(str(tmp_path / "wal.db"))
    try:
        assert store._conn.execute("PRAGMA journal_mode").fetchone()[0] == "wal"
    finally:
        store.close()


def test_a_serving_process_picks_up_an_ingest(tmp_path):
    """The other process is modelled by a second connection to the same file (WAL)."""
    db = tmp_path / "reload.db"
    # gate and novelty floor opened: this test is about the reload path, not the refusal path
    settings = dataclasses.replace(
        SETTINGS,
        api_token="",
        confidence_theta=0.0,
        refusal_novelty_floor=0.0,
        reload_ms=1.0,
    )
    store = Store(str(db), settings=settings)
    for name, content in MINI_CORPUS.items():
        doc = tmp_path / name
        doc.write_text(content, encoding="utf-8")
        tenant = "other" if name == "hostel-rules.md" else "campus"
        store.upsert_document(tenant_id=tenant, path=doc)
    store.close()
    state = ServerState(str(db), settings=settings)
    server = make_server(("127.0.0.1", 0), state)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    base = f"http://127.0.0.1:{server.server_address[1]}"
    try:
        before = _ask(base, "library loans")
        assert all(citation["doc_id"] != "library-rules" for citation in before["citations"])
        other = Store(str(db))
        try:
            doc = tmp_path / "library-rules.md"
            doc.write_text(NEW_DOC, encoding="utf-8")
            other.upsert_document(tenant_id="campus", path=doc)
        finally:
            other.close()
        time.sleep(0.005)  # longer than reload_ms, so the next request may look
        answer = _ask(base, "library loans")
        assert answer["refused"] is False
        assert answer["citations"][0]["doc_id"] == "library-rules"
    finally:
        server.shutdown()
        server.server_close()
        state.store.close()


def _ask(base_url, query):
    url = f"{base_url}/v1/ask?q={urllib.parse.quote(query)}"
    with urllib.request.urlopen(url, timeout=5) as response:
        return json.loads(response.read())
