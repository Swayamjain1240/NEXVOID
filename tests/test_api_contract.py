"""HTTP contract tests for the M18 surface (FR-20..FR-23): POST, metrics, health, methods.

Same harness as `tests/test_server.py`: a real server thread on an ephemeral port over the mini
corpus, in a temp DB. These tests pin the *shape* the SRS now promises, so a change to the shape is
a failing test rather than a stale paragraph.
"""

from __future__ import annotations

import dataclasses
import json
import threading
import urllib.error
import urllib.request
from http.server import ThreadingHTTPServer

import pytest

from atlas.config import SETTINGS
from atlas.metrics import METRICS
from atlas.server import ServerState, make_handler
from atlas.store import Store
from tests.conftest import MINI_CORPUS

ANSWER_KEYS = {
    "query", "text", "refused", "confidence", "citations", "mode", "degraded",
    "refusal_reason", "unseen", "as_of", "effective_from", "trace_id", "timings",
}


def _boot(tmp_path, settings):
    db = tmp_path / "atlas-contract.db"
    store = Store(str(db), settings=settings)
    for name, content in MINI_CORPUS.items():
        doc = tmp_path / name
        doc.write_text(content, encoding="utf-8")
        tenant = "other" if name == "hostel-rules.md" else "campus"
        store.upsert_document(tenant_id=tenant, path=doc)
    store.close()
    state = ServerState(str(db), settings=settings)
    server = ThreadingHTTPServer(("127.0.0.1", 0), make_handler(state))
    threading.Thread(target=server.serve_forever, daemon=True).start()
    return f"http://127.0.0.1:{server.server_address[1]}", server, state


@pytest.fixture
def api(tmp_path):
    """Open gate: the mini corpus scores below theta, and refusal is measured on the real corpus."""
    settings = dataclasses.replace(SETTINGS, api_token="test-token", confidence_theta=0.0)
    base_url, server, state = _boot(tmp_path, settings)
    METRICS.reset()
    yield base_url
    server.shutdown()
    state.store.close()
    server.server_close()


def _body_bytes(body):
    if body is None:
        return None
    return body if isinstance(body, bytes) else json.dumps(body).encode()


def _call(url, *, method="GET", body=None, token="test-token"):
    data = _body_bytes(body)
    request = urllib.request.Request(url, data=data, method=method)
    if token:
        request.add_header("Authorization", f"Bearer {token}")
    if data is not None:
        request.add_header("Content-Type", "application/json")
    try:
        with urllib.request.urlopen(request, timeout=5) as response:
            return response.status, json.loads(response.read())
    except urllib.error.HTTPError as error:
        return error.code, json.loads(error.read())


def test_post_ask_returns_the_same_answer_shape_as_get(api):
    status, payload = _call(f"{api}/v1/ask", method="POST", body={"query": "attendance policy"})
    assert status == 200
    assert set(payload) >= ANSWER_KEYS
    assert set(payload["timings"]) >= {"retrieve_ms", "gate_ms", "extract_ms", "total_ms"}
    get_status, get_payload = _call(f"{api}/v1/ask?q=attendance%20policy")
    assert get_status == 200 and set(get_payload) == set(payload)


def test_post_ask_rejects_a_body_that_is_not_json(api):
    status, payload = _call(f"{api}/v1/ask", method="POST", body=b"not json")
    assert status == 400 and payload["error"]["code"] == "INPUT_INVALID"


def test_post_ask_rejects_unknown_fields(api):
    status, payload = _call(
        f"{api}/v1/ask", method="POST", body={"query": "attendance", "tenant_id": "other"}
    )
    assert status == 400 and "tenant_id" in payload["error"]["message"]


def test_post_ask_rejects_a_missing_query(api):
    status, payload = _call(f"{api}/v1/ask", method="POST", body={"mode": "hybrid"})
    assert status == 400 and payload["error"]["code"] == "INPUT_INVALID"


def test_post_ask_requires_the_bearer_token(api):
    status, payload = _call(f"{api}/v1/ask", method="POST", body={"query": "x"}, token=None)
    assert status == 401 and payload["error"]["code"] == "AUTH_REQUIRED"


def test_as_of_is_echoed_and_a_bad_date_is_a_400(api):
    """FR-29: the date is part of the request contract, and a malformed one is refused."""
    status, payload = _call(f"{api}/v1/ask?q=attendance&as_of=2024-01-01")
    assert status == 200 and payload["as_of"] == "2024-01-01"
    bad_status, bad_payload = _call(f"{api}/v1/ask?q=attendance&as_of=2024-13-01")
    assert bad_status == 400 and bad_payload["error"]["code"] == "INPUT_INVALID"
    post_status, post_payload = _call(
        f"{api}/v1/ask", method="POST", body={"query": "attendance", "as_of": "2024-01-01"}
    )
    assert post_status == 200 and post_payload["as_of"] == "2024-01-01"


def test_health_is_an_alias_of_healthz(api):
    health = _call(f"{api}/health", token=None)
    healthz = _call(f"{api}/v1/healthz", token=None)
    assert health == healthz


def test_metrics_counts_the_requests_it_just_served(api):
    _call(f"{api}/v1/ask?q=attendance%20percentage")
    status, payload = _call(f"{api}/v1/metrics", token=None)
    assert status == 200
    assert payload["routes"]["GET /v1/ask"]["count"] == 1
    assert payload["routes"]["GET /v1/ask"]["latency"]["p95_ms"] >= 0.0


def test_metrics_never_carries_request_content(api):
    _call(f"{api}/v1/ask?q=seventy%20five%20percent%20condonation")
    _, payload = _call(f"{api}/v1/metrics", token=None)
    assert "condonation" not in json.dumps(payload).lower()


def test_unsupported_method_gets_an_error_envelope_not_html(api):
    status, payload = _call(f"{api}/v1/ask", method="PUT", body={})
    assert status == 405 and payload["error"]["code"] == "METHOD_NOT_ALLOWED"


def test_documents_ingest_is_not_offered_over_http(api):
    # ADR-0012: ingest stays a CLI operation; the absence is part of the published contract.
    status, payload = _call(f"{api}/v1/documents", method="POST", body={"tenant": "campus"})
    assert status == 404 and payload["error"]["code"] == "NOT_FOUND"


def test_demo_page_is_served_for_people(api):
    """FR-22: `/` is a page a person can use without reading anything else."""
    with urllib.request.urlopen(f"{api}/", timeout=10) as response:
        status, body = response.status, response.read().decode("utf-8")
    assert status == 200
    lowered = body.lower()
    assert "<form" in lowered and 'type="search"' in lowered
    assert "citations" in lowered
