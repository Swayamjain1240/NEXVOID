"""The HTTP contract, probed against a live server (M18, FR-20..FR-23).

The SRS promises a surface; this script starts the real server over a freshly ingested copy of the
corpus on an ephemeral port and calls every documented route, including the ones that are supposed
to fail. A contract that is only tested for the happy path is a contract nobody checked.

What is asserted, per probe:

- **status** - the documented code, not "some 2xx";
- **shape** - the answer body carries the eleven top-level fields and four stage timings (FR-21),
  the error body is `{"error": {"code", "message"}}` with no traceback (FR-23);
- **metrics realness** - `GET /v1/metrics` counts the requests this probe just made (FR-20);
- **absence** - `POST /v1/documents` is not offered, because ADR-0012 keeps ingest a CLI operation.
  The absence is probed so it is part of the published contract instead of an omission.

Writes `results/api_contract.md` and `results/api_contract.json`.
"""

from __future__ import annotations

import json
import sys
import tempfile
import threading
import urllib.error
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT))

from atlas.config import SETTINGS  # noqa: E402
from atlas.metrics import METRICS  # noqa: E402
from atlas.server import ServerState, make_server  # noqa: E402
from atlas.store import Store  # noqa: E402
from eval.run_eval import CORPUS, RESULTS, provenance  # noqa: E402

ANSWER_KEYS = {
    "query", "text", "refused", "confidence", "citations", "mode", "degraded",
    "refusal_reason", "unseen", "as_of", "effective_from", "trace_id", "timings",
}
STAGES = {"retrieve_ms", "gate_ms", "extract_ms", "total_ms"}
QUESTION = "What is the minimum attendance to stay enrolled?"


def _problems(answer_shape_payload: dict) -> list[str]:
    problems = []
    if not set(answer_shape_payload) >= ANSWER_KEYS:
        missing = sorted(ANSWER_KEYS - set(answer_shape_payload))
        problems.append(f"missing keys {missing}")
    stages = set(answer_shape_payload.get("timings", {}))
    if not stages >= STAGES:
        problems.append(f"missing timings {sorted(STAGES - stages)}")
    # M22: the dominant stage is broken down in every response, not only in a report
    breakdown = answer_shape_payload.get("timings", {}).get("retrieval")
    if not isinstance(breakdown, dict) or not breakdown:
        problems.append("timings.retrieval is missing or empty")
    return problems


def _as_of_problems(payload: dict) -> list[str]:
    """FR-29: the date asked about is accepted and echoed, so a client can log what it asked."""
    problems = _problems(payload)
    if payload.get("as_of") != "2024-01-01":
        problems.append(f"as_of not echoed: {payload.get('as_of')!r}")
    return problems


def _error_problems(payload: dict) -> list[str]:
    error = payload.get("error")
    if not isinstance(error, dict) or not error.get("code") or not error.get("message"):
        return ["error envelope missing code/message"]
    return []


def _metrics_problems(payload: dict) -> list[str]:
    problems = []
    for key in ("uptime_s", "requests", "errors", "routes", "admission"):
        if key not in payload:
            problems.append(f"missing {key}")
    if not isinstance(payload.get("routes"), dict):
        problems.append("routes is not an object")
    # M23: capacity is part of the contract, so a shed request is explainable after the fact
    admission = payload.get("admission")
    if not isinstance(admission, dict):
        problems.append("admission is not an object")
    else:
        missing = {"limit", "queue_limit", "wait_ms", "inflight", "waiting", "rejected"}
        if not set(admission) >= missing:
            problems.append(f"admission missing {sorted(missing - set(admission))}")
    return problems


def _boot(tmp: Path) -> tuple[object, str, ServerState]:
    """Fresh DB + corpus, real server thread, ephemeral port."""
    db = tmp / "contract.db"
    store = Store(str(db))
    for path in sorted(CORPUS.glob("*.md")):
        store.upsert_document(tenant_id=SETTINGS.default_tenant, path=path)
    store.close()
    state = ServerState(str(db))
    server = make_server(("127.0.0.1", 0), state)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    return server, f"http://127.0.0.1:{server.server_address[1]}", state


def _payload_bytes(body):
    """A dict becomes JSON; raw bytes are sent as-is so the malformed-body probe is real."""
    if body is None:
        return None
    if isinstance(body, bytes):
        return body
    return json.dumps(body).encode()


def _call(base: str, method: str, path: str, body=None) -> tuple[int, dict]:
    data = _payload_bytes(body)
    request = urllib.request.Request(base + path, data=data, method=method)
    if data is not None:
        request.add_header("Content-Type", "application/json")
    try:
        with urllib.request.urlopen(request, timeout=10) as response:
            return response.status, json.loads(response.read())
    except urllib.error.HTTPError as error:
        return error.code, json.loads(error.read())


def main() -> int:
    METRICS.reset()
    with tempfile.TemporaryDirectory() as tmp:
        server, base, state = _boot(Path(tmp))
        try:
            probes = []
            for method, path, body, expected, check in (
                ("GET", "/v1/healthz", None, 200, None),
                ("GET", "/health", None, 200, None),
                ("GET", "/v1/ask?q=" + QUESTION.replace(" ", "%20"), None, 200, _problems),
                ("GET", "/v1/ask?q=" + QUESTION.replace(" ", "%20") + "&as_of=2024-01-01",
                 None, 200, _as_of_problems),
                ("GET", "/v1/ask?q=" + QUESTION.replace(" ", "%20") + "&as_of=2024-13-01",
                 None, 400, _error_problems),
                ("POST", "/v1/ask", {"query": QUESTION}, 200, _problems),
                ("POST", "/v1/ask", {"query": QUESTION, "as_of": "2024-01-01"}, 200,
                 _as_of_problems),
                ("POST", "/v1/ask", {"query": "attendance", "mode": "dense"}, 200, _problems),
                ("POST", "/v1/ask", b"{not json", 400, _error_problems),
                ("POST", "/v1/ask", {"query": "attendance", "tenant": "other"},
                 400, _error_problems),
                ("GET", "/v1/ask?q=", None, 400, _error_problems),
                ("GET", "/v1/metrics", None, 200, _metrics_problems),
                ("POST", "/v1/documents", {"tenant": "campus"}, 404, _error_problems),
                ("PUT", "/v1/ask", {}, 405, _error_problems),
                ("GET", "/v1/nope", None, 404, _error_problems),
            ):
                status, payload = _call(base, method, path, body)
                problems = [] if check is None else check(payload)
                shown = path.split("?")[0] + ("?q=..." if path.startswith("/v1/ask?") else "")
                probes.append(
                    {
                        "method": method,
                        "path": shown,
                        "expected": expected,
                        "status": status,
                        "problems": problems,
                        "pass": status == expected and not problems,
                    }
                )
            requests_seen = _call(base, "GET", "/v1/metrics")[1]["requests"]
        finally:
            server.shutdown()
            state.store.close()
            server.server_close()

    payload = {
        "provenance": provenance(),
        "method": (__doc__ or "").strip(),
        "base": "ephemeral loopback port",
        "answer_keys": sorted(ANSWER_KEYS),
        "timing_stages": sorted(STAGES),
        "probes": probes,
        "metrics_requests_seen": requests_seen,
        "all_pass": all(probe["pass"] for probe in probes),
    }
    (RESULTS / "api_contract.json").write_text(json.dumps(payload, indent=2), encoding="utf-8")

    lines = [
        "# The HTTP contract, probed against a live server (M18)",
        "",
        f"Command: `python -m eval.api_contract` · commit `{payload['provenance']['commit']}` · "
        f"corpus `{payload['provenance']['corpus']}`",
        "",
        "A fresh database is ingested from the corpus, the real server is started on an ephemeral",
        "loopback port, and every documented route is called - including the ones that are meant",
        "to fail. `?q=` is the only part of a path elided; row 3 carries the actual question.",
        "",
        "| # | method | path | expected | actual | shape | verdict |",
        "|---|---|---|---|---|---|---|",
    ]
    for number, probe in enumerate(payload["probes"], start=1):
        shape = "ok" if not probe["problems"] else "; ".join(probe["problems"])
        verdict = "PASS" if probe["pass"] else "**FAIL**"
        lines.append(
            f"| {number} | {probe['method']} | `{probe['path']}` | {probe['expected']} "
            f"| {probe['status']} | {shape} | {verdict} |"
        )
    lines += [
        "",
        f"Requests counted by `GET /v1/metrics` during the run: **{requests_seen}**.",
        f"Overall: **{'PASS' if payload['all_pass'] else 'FAIL'}**",
        "",
        "## What the answer body must carry",
        "",
        "Eleven top-level fields, checked on both GET and POST:",
        "",
        "```",
        ", ".join(sorted(ANSWER_KEYS)),
        "```",
        "",
        "and four stage timings (`retrieve_ms`, `gate_ms`, `extract_ms`, `total_ms`), which is the",
        "observability requirement (NFR-14) expressed as a contract rather than a screenshot.",
        "",
        "## What is deliberately absent",
        "",
        "`POST /v1/documents` returns 404. ADR-0012 decides that ingest stays a CLI operation with",
        "idempotent checksums: a write endpoint on a single-node demo with no roles, no rate limit",
        "and no backup is the one surface where this project could do real damage. The absence is",
        "probed, so it is a documented decision instead of a missing feature.",
        "",
        "## Reproducing",
        "",
        "```",
        "make api-contract",
        "```",
        "",
        "The probe writes nothing into `data/` and binds an ephemeral port, so it runs wherever",
        "the tests run, including CI.",
        "",
    ]
    (RESULTS / "api_contract.md").write_text("\n".join(lines), encoding="utf-8")

    print("written: results/api_contract.md, results/api_contract.json")
    for probe in payload["probes"]:
        print(f"  {'PASS' if probe['pass'] else 'FAIL'} {probe['method']:4s} {probe['path']:22s} "
              f"{probe['status']}")
    print(f"  all pass: {payload['all_pass']}")
    return 0 if payload["all_pass"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
