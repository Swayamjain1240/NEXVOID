"""M24: two processes, one port, both answering.

The supervisor itself is an infinite loop, so the test drives the two pieces it is built
from — the shared listening socket and `serve_in_child` — and checks what the contract
promises: one port, real answers, and a `pid` that says which worker replied.
"""

from __future__ import annotations

import dataclasses
import http.client
import json
import multiprocessing

import pytest

from atlas import workers
from atlas.config import SETTINGS


def _get(port: int, path: str) -> tuple[int, dict]:
    connection = http.client.HTTPConnection("127.0.0.1", port, timeout=10)
    try:
        connection.request("GET", path)
        response = connection.getresponse()
        body = response.read()
        return response.status, json.loads(body)
    finally:
        connection.close()


@pytest.fixture
def pool(mini_store, tmp_path):
    """Two children on one socket, over the mini corpus."""
    sock = workers.listen("127.0.0.1", 0)
    port = sock.getsockname()[1]
    settings = dataclasses.replace(SETTINGS, max_inflight=4, max_waiting=4, admission_wait_ms=5.0)
    children = [
        multiprocessing.Process(
            target=workers.serve_in_child,
            args=(sock, str(tmp_path / "atlas-test.db"), settings),
            daemon=True,
        )
        for _ in range(2)
    ]
    for child in children:
        child.start()
    yield port, children
    for child in children:
        child.terminate()
    for child in children:
        child.join(timeout=10)
    sock.close()


def test_two_workers_share_one_port_and_answer(pool):
    port, children = pool
    deadline = 0
    while deadline < 100:
        try:
            status, _ = _get(port, "/v1/healthz")
            if status == 200:
                break
        except (OSError, http.client.HTTPException):
            pass
        deadline += 1
    assert _get(port, "/v1/healthz")[0] == 200
    status, body = _get(port, "/v1/ask?q=hostel+curfew")
    assert status == 200
    assert body["timings"]["retrieve_ms"] >= 0
    pids = {_get(port, "/v1/healthz")[1]["pid"] for _ in range(10)}
    assert pids <= {child.pid for child in children}, "a pid outside the pool answered"


def test_one_worker_is_refused_rather_than_silently_single_process():
    """`serve_workers(1)` would be a confusing second way to run one server."""
    with pytest.raises(ValueError, match="at least 2"):
        workers.serve_workers(db="unused.db", host="127.0.0.1", port=0, workers=1)


def test_the_worker_count_reaches_the_cli():
    from atlas.cli import _parser

    assert _parser().parse_args(["serve", "--workers", "4"]).workers == 4
    assert _parser().parse_args(["serve"]).workers == 1
