"""M25: the exact cache - its key, its counters, and the leak it must not have.

The unit tests pin the key and the bound; the HTTP tests pin what a caller sees (a hit says so,
carries a fresh trace id and no retrieval timings) and that a second tenant is never served the
first tenant's answer for the same question.
"""

from __future__ import annotations

import dataclasses
import json
import threading
import urllib.parse
import urllib.request

import pytest

from atlas.cache import AnswerCache, CacheKey
from atlas.config import SETTINGS
from atlas.interfaces import Answer
from atlas.server import ServerState, make_server
from atlas.store import Store
from tests.conftest import MINI_CORPUS


def _answer(text: str = "cached body") -> Answer:
    return Answer(
        query="curfew",
        text=text,
        citations=(),
        confidence=0.9,
        refused=False,
        mode="hybrid",
        degraded=False,
        timings_ms={},
        trace_id="0" * 16,
    )


def _key(**overrides) -> CacheKey:
    fields = {
        "tenant": "campus",
        "query": "curfew",
        "mode": "hybrid",
        "as_of": "",
        "corpus_hash": "h",
    }
    fields.update(overrides)
    return CacheKey(**fields)


def test_every_part_of_the_key_separates_entries():
    cache = AnswerCache(capacity=8)
    cache.put(_key(), _answer())
    assert cache.get(_key()).text == "cached body"
    for field in ("tenant", "query", "mode", "as_of", "corpus_hash"):
        assert cache.get(_key(**{field: "other"})) is None, field


def test_the_bound_evicts_the_oldest_and_says_so():
    cache = AnswerCache(capacity=2)
    for query in ("a", "b", "c"):
        cache.put(_key(query=query), _answer(query))
    snapshot = cache.snapshot()
    assert snapshot["size"] == 2 and snapshot["evictions"] == 1
    assert cache.get(_key(query="a")) is None


def test_zero_capacity_disables_it_without_a_second_flag():
    cache = AnswerCache(capacity=0)
    cache.put(_key(), _answer())
    assert cache.get(_key()) is None
    assert cache.snapshot()["size"] == 0


def test_the_counters_report_the_rate_not_just_the_counts():
    cache = AnswerCache(capacity=4)
    cache.get(_key())  # miss
    cache.put(_key(), _answer())
    cache.get(_key())  # hit
    snapshot = cache.snapshot()
    assert (snapshot["hits"], snapshot["misses"]) == (1, 1)
    assert snapshot["hit_rate"] == pytest.approx(0.5)


def _boot(tmp_path):
    settings = dataclasses.replace(SETTINGS, api_token="", confidence_theta=0.0)
    db = tmp_path / "atlas-cache.db"
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
    return f"http://127.0.0.1:{server.server_address[1]}", server, state


@pytest.fixture
def api(tmp_path):
    base_url, server, state = _boot(tmp_path)
    yield base_url, state
    server.shutdown()
    server.server_close()
    state.store.close()


def _ask(base_url, query, *, tenant="campus", as_of="", mode=""):
    url = f"{base_url}/v1/ask?q={urllib.parse.quote(query)}"
    if as_of:
        url += f"&as_of={as_of}"
    if mode:
        url += f"&mode={mode}"
    request = urllib.request.Request(url)
    request.add_header("X-Tenant", tenant)
    with urllib.request.urlopen(request, timeout=5) as response:
        return json.loads(response.read())


def test_a_repeat_question_is_served_from_the_cache(api):
    base_url, _ = api
    first = _ask(base_url, "attendance policy")
    second = _ask(base_url, "attendance policy")
    assert first["cache"] == "miss" and second["cache"] == "hit"
    assert second["text"] == first["text"]
    assert second["citations"] == first["citations"]
    assert second["confidence"] == first["confidence"]
    # a hit is a new request: it gets its own trace id, and it does not claim the retrieval
    # timings of the request that populated the cache
    assert second["trace_id"] != first["trace_id"]
    assert second["timings"]["retrieve_ms"] == 0.0


def test_a_tenant_never_receives_another_tenants_cached_answer(api):
    base_url, _ = api
    campus = _ask(base_url, "attendance policy", tenant="campus")
    assert campus["refused"] is False
    # 'other' holds only the hostel document, so the same question must be refused there
    other = _ask(base_url, "attendance policy", tenant="other")
    assert other["refused"] is True
    assert other["text"] != campus["text"]
    again = _ask(base_url, "attendance policy", tenant="other")
    assert again["cache"] == "hit" and again["refused"] is True
    assert again["text"] == other["text"]


def test_a_changed_document_is_a_different_key_not_a_stale_hit(api, tmp_path):
    base_url, state = api
    before = _ask(base_url, "attendance for every course")
    doc = tmp_path / "attendance-policy.md"
    doc.write_text(
        doc.read_text(encoding="utf-8").replace("seventy-five", "sixty-six"), encoding="utf-8"
    )
    state.store.upsert_document(tenant_id="campus", path=doc)
    state.index.invalidate("campus")
    after = _ask(base_url, "attendance for every course")
    assert after["cache"] == "miss"
    assert after["text"] != before["text"]
    assert "sixty-six" in after["text"]


def test_the_date_and_the_mode_are_part_of_the_key(api):
    base_url, _ = api
    undated = _ask(base_url, "attendance policy")
    dated = _ask(base_url, "attendance policy", as_of="2024-01-01")
    sparse = _ask(base_url, "attendance policy", mode="sparse")
    assert undated["cache"] == "miss"
    assert dated["cache"] == "miss" and dated["as_of"] == "2024-01-01"
    assert sparse["cache"] == "miss" and sparse["mode"] == "sparse"


def test_metrics_publishes_the_cache_counters(api):
    base_url, _ = api
    _ask(base_url, "attendance policy")
    _ask(base_url, "attendance policy")
    with urllib.request.urlopen(f"{base_url}/v1/metrics", timeout=5) as response:
        payload = json.loads(response.read())
    cache = payload["cache"]
    assert cache["hits"] == 1 and cache["misses"] == 1
    assert cache["size"] == 1 and cache["capacity"] == SETTINGS.cache_size
    assert cache["hit_rate"] == pytest.approx(0.5)
