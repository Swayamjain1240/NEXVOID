"""Does adding processes add throughput? (M24, NFR-01)

One port, 1, 2 and 4 worker processes, the same 10-user load each time. The question is not
"is more better" but where it stops, and whether a per-worker cache costs enough warm-up to
eat the gain.

Run: python -m bench.workers
"""

from __future__ import annotations

import dataclasses
import http.client
import json
import multiprocessing
import sys
import tempfile
import threading
import time
from pathlib import Path
from urllib.parse import quote

_SRC = Path(__file__).resolve().parents[1] / "src"
if str(_SRC) not in sys.path:
    sys.path.insert(0, str(_SRC))

from atlas import workers  # noqa: E402
from atlas.config import SETTINGS  # noqa: E402
from atlas.server import ServerState, make_server  # noqa: E402
from bench.sweep import build_database, run_load  # noqa: E402
from eval.run_eval import RESULTS, load_questions, provenance  # noqa: E402

LEVELS = (1, 2, 4)
USERS = 10
PER_USER = 100


def _health(port: int) -> dict | None:
    connection = http.client.HTTPConnection("127.0.0.1", port, timeout=5)
    try:
        connection.request("GET", "/v1/healthz")
        response = connection.getresponse()
        body = response.read()
        return json.loads(body) if response.status == 200 else None
    except (OSError, http.client.HTTPException):
        return None
    finally:
        connection.close()


def _ask(port: int, query: str) -> int:
    connection = http.client.HTTPConnection("127.0.0.1", port, timeout=10)
    try:
        connection.request("GET", f"/v1/ask?q={quote(query)}")
        response = connection.getresponse()
        response.read()
        return response.status
    finally:
        connection.close()


def _wait_ready(port: int, timeout: float = 15.0) -> None:
    deadline = time.time() + timeout
    while time.time() < deadline:
        if _health(port) is not None:
            return
        time.sleep(0.05)
    raise RuntimeError(f"no worker answered on port {port} within {timeout}s")


def boot_single(db: Path) -> tuple[object, int, ServerState]:
    # M25: cache off, so this stays a curve about processes rather than about repeated questions
    state = ServerState(str(db), settings=dataclasses.replace(SETTINGS, cache_size=0))
    server = make_server(("127.0.0.1", 0), state)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    return server, server.server_address[1], state


def boot_pool(db: Path, count: int) -> tuple[object, int, list]:
    """One listening socket, `count` children, and the socket to close afterwards."""
    sock = workers.listen("127.0.0.1", 0)
    port = sock.getsockname()[1]
    children = [
        multiprocessing.Process(
            target=workers.serve_in_child,
            args=(sock, str(db), dataclasses.replace(SETTINGS, cache_size=0)),
            daemon=True,
        )
        for _ in range(count)
    ]
    for child in children:
        child.start()
    return sock, port, children


def measure(db: Path, count: int, questions: list[str]) -> dict:
    if count == 1:
        server, port, state = boot_single(db)
        sock, children = None, []
    else:
        sock, port, children = boot_pool(db, count)
        server, state = None, None
    try:
        _wait_ready(port)
        # warm every worker's own view: each has its own index, so this is not shared work
        for _ in range(count * 3):
            _ask(port, questions[0])
        row = run_load(f"http://127.0.0.1:{port}", questions, users=USERS, per_user=PER_USER)
        pids = set()
        for _ in range(20):
            body = _health(port)
            if body and "pid" in body:
                pids.add(body["pid"])
        row["workers"] = count
        row["pids_observed"] = len(pids)
    finally:
        if server is not None:
            server.shutdown()
            server.server_close()
            state.store.close()
        if sock is not None:
            for child in children:
                child.terminate()
            for child in children:
                child.join(timeout=5)
            sock.close()
    return row


def render(rows: list[dict], payload: dict) -> list[str]:
    one = rows[0]
    best = max(rows, key=lambda row: row["rps"])
    return [
        "# Worker processes — 1, 2 and 4 behind one port (M24, NFR-01)",
        "",
        f"Command: `python -m bench.workers` · commit `{payload['provenance']['commit']}` · "
        f"platform: {payload['provenance']['platform']}",
        "",
        f"{USERS} client processes, {PER_USER} requests each, one listening socket shared by "
        "the workers, real `GET /v1/ask` through the production handler.",
        "",
        "| workers | requests | served | p50 ms | p95 ms | rps | pids observed |",
        "|---|---|---|---|---|---|---|",
    ] + [
        f"| {row['workers']} | {row['requests']} | {row['served']} | {row['p50_ms']:.2f} "
        f"| {row['p95_ms']:.2f} | {row['rps']:.1f} | {row['pids_observed']} |"
        for row in rows
    ] + [
        "",
        f"Throughput {one['rps']:.0f} rps at one worker to {best['rps']:.0f} rps at "
        f"{best['workers']} ({best['rps'] / one['rps']:.2f}×), p95 {one['p95_ms']:.2f} → "
        f"{best['p95_ms']:.2f} ms.",
        "",
        "## Interpretation",
        "",
        f"- Latency and throughput scale differently here, and the load explains it: at a fixed "
        f"{USERS} users offering one request at a time, throughput cannot exceed "
        f"{USERS} / service time, so the *client* caps it long before the workers do. p95 "
        f"{one['p95_ms']:.2f} → {best['p95_ms']:.2f} ms is the real result; more workers mainly "
        "remove queueing, not work.",
        "- Each worker owns its own `Store` connection, index and tenant views, so the first "
        "requests to a new worker pay the warm-up. The script warms every worker before "
        "measuring; a cold pool would look worse than this.",
        "- `/v1/metrics` answers from whichever worker accepted the connection. With more than "
        "one worker its counters describe one process, not the pool, and `pid` in "
        "`/v1/healthz` is how you know which (L13). No number here is an average across "
        "processes pretending to be one system.",
        "- The client is the other limit: ten processes on one machine contend for the same "
        "cores the server uses, so the curve measures this laptop's ability to both offer and "
        "serve load, not a datacentre.",
        "",
        "- `--workers N` is the operator surface (`atlas.cli serve --workers N`); the parent "
        "supervises and restarts a child that dies.",
        "",
    ]


def main() -> int:
    answerable, _ = load_questions()
    questions = [row["query"] for row in answerable]
    db = Path(tempfile.mkdtemp()) / "workers.db"
    build_database(db)
    rows = [measure(db, count, questions) for count in LEVELS]
    payload = {"provenance": provenance(), "rows": rows}
    payload["notes"] = render(rows, payload)
    RESULTS.mkdir(exist_ok=True)
    (RESULTS / "workers.json").write_text(json.dumps(payload, indent=2), encoding="utf-8")
    (RESULTS / "workers.md").write_text("\n".join(payload["notes"]) + "\n", encoding="utf-8")
    for row in rows:
        print(f"  {row['workers']} worker(s): p95 {row['p95_ms']:.2f} ms · {row['rps']:.0f} rps "
              f"· {row['pids_observed']} pids")
    print("written: results/workers.md, results/workers.json")
    return 0


if __name__ == "__main__":
    sys.exit(main())
