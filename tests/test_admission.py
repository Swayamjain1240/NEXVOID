"""M23: the bound itself — concurrency limit, queue bound, wait deadline, and the 503.

These test the controller directly and then through the real HTTP server, because the
interesting failure is not "does the semaphore count" but "does an overloaded server still
answer liveness, and does it refuse with a code instead of hanging".
"""

from __future__ import annotations

import dataclasses
import json
import threading
import time

import pytest

from atlas.admission import Admission
from atlas.config import SETTINGS
from atlas.server import ServerState, make_server


def test_the_limit_bounds_concurrency():
    gate = Admission(limit=2, queue_limit=0, wait_ms=0)
    assert gate.acquire() and gate.acquire()
    assert gate.snapshot()["inflight"] == 2
    assert gate.acquire() is False  # third has nowhere to wait: shed immediately
    gate.release()
    assert gate.acquire() is True
    gate.release()
    gate.release()
    assert gate.snapshot()["inflight"] == 0


def test_the_queue_bound_is_separate_from_the_concurrency_limit():
    """One slot, one waiter: the waiter is admitted when the slot frees, a third is refused."""
    gate = Admission(limit=1, queue_limit=1, wait_ms=200)
    assert gate.acquire() is True
    results: list[bool] = []

    def wait() -> None:
        results.append(gate.acquire())
        if results[-1]:
            gate.release()

    waiter = threading.Thread(target=wait)
    waiter.start()
    deadline = time.perf_counter() + 2.0
    while gate.snapshot()["waiting"] == 0 and time.perf_counter() < deadline:
        time.sleep(0.005)
    # the waiter is parked; the queue is full, so this one cannot even wait
    assert gate.acquire() is False
    gate.release()
    waiter.join(timeout=2.0)
    assert results == [True]
    assert gate.snapshot()["rejected"] == 1


def test_a_waiter_gives_up_after_the_deadline():
    gate = Admission(limit=1, queue_limit=4, wait_ms=20)
    assert gate.acquire() is True
    started = time.perf_counter()
    assert gate.acquire() is False
    waited = (time.perf_counter() - started) * 1000.0
    assert 15.0 <= waited < 500.0, f"waited {waited:.1f} ms for a 20 ms deadline"
    snapshot = gate.snapshot()
    assert snapshot["rejected"] == 1 and snapshot["admitted"] == 1
    assert snapshot["inflight"] == 1  # the timeout did not consume the slot
    gate.release()


def test_the_bound_is_configurable_from_the_environment(monkeypatch):
    monkeypatch.setenv("ATLAS_MAX_INFLIGHT", "4")
    monkeypatch.setenv("ATLAS_MAX_WAITING", "0")
    monkeypatch.setenv("ATLAS_ADMISSION_WAIT_MS", "2.5")
    settings = SETTINGS.__class__()
    assert settings.max_inflight == 4
    assert settings.max_waiting == 0
    assert settings.admission_wait_ms == pytest.approx(2.5)
    gate = Admission(limit=settings.max_inflight, queue_limit=settings.max_waiting,
                     wait_ms=settings.admission_wait_ms)
    assert gate.snapshot()["limit"] == 4


def test_a_malformed_bound_refuses_to_start(monkeypatch):
    monkeypatch.setenv("ATLAS_MAX_INFLIGHT", "lots")
    with pytest.raises(ValueError, match="ATLAS_MAX_INFLIGHT"):
        SETTINGS.__class__()


def test_the_snapshot_reports_the_rate_not_just_the_counts():
    gate = Admission(limit=1, queue_limit=0, wait_ms=0)
    assert gate.acquire() is True
    assert gate.acquire() is False
    gate.release()
    snapshot = gate.snapshot()
    assert snapshot["reject_rate"] == pytest.approx(0.5)
    assert snapshot["limit"] == 1 and snapshot["queue_limit"] == 0


@pytest.fixture
def tight_server(tmp_path, mini_store):
    """A server whose bound is one slot and no queue, so overload is deterministic."""
    settings = dataclasses.replace(SETTINGS, max_inflight=1, max_waiting=0,
                                   admission_wait_ms=0.0)
    state = ServerState(str(tmp_path / "atlas-test.db"), settings=settings)
    server = make_server(("127.0.0.1", 0), state)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    yield server, state
    server.shutdown()
    server.server_close()
    state.store.close()


def _get(port: int, path: str) -> tuple[int, dict, dict]:
    import http.client

    connection = http.client.HTTPConnection("127.0.0.1", port, timeout=5)
    try:
        connection.request("GET", path)
        response = connection.getresponse()
        body = response.read()
        headers = {key.lower(): value for key, value in response.getheaders()}
        return response.status, json.loads(body), headers
    finally:
        connection.close()


def test_an_overloaded_server_sheds_the_question_and_keeps_liveness(tight_server):
    server, state = tight_server
    port = server.server_address[1]
    assert state.admission.acquire() is True  # occupy the only slot, as a slow request would
    try:
        status, body, headers = _get(port, "/v1/ask?q=hostel+curfew")
        assert status == 503
        assert body["error"]["code"] == "OVERLOADED"
        assert headers["retry-after"] == "1"
        # liveness and metrics are the two things an operator needs *during* the overload
        assert _get(port, "/v1/healthz")[0] == 200
        metrics = _get(port, "/v1/metrics")[1]
        assert metrics["admission"]["rejected"] == 1
        assert metrics["admission"]["inflight"] == 1
    finally:
        state.admission.release()
    # the slot is free again, so the same question is answered
    assert _get(port, "/v1/ask?q=hostel+curfew")[0] == 200
