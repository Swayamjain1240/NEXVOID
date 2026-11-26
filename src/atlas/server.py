"""M5 HTTP JSON API (ATL-SwDD; SRS FR-19..FR-23, NFR-01, NFR-09, NFR-14): stdlib only.

Routes (the handler lives in `handler.py`):
    GET  /                 one-page demo (web/demo.html), no auth
    GET  /health           liveness; alias of /v1/healthz (FR-20)
    GET  /v1/healthz       liveness, no auth
    GET  /v1/ask?q=...     the product: cited answer or refusal (JSON)
    POST /v1/ask           the same answer from {"query", "mode"} (FR-20)
    GET  /v1/metrics       process counters and latency summary (FR-20)

Headers:
    Authorization: Bearer <token>   required when ATLAS_API_TOKEN is set in the env
    X-Tenant: <tenant>              defaults to the configured default tenant

Contract details the tests pin: empty/stopword-only query -> 400 with a machine-readable error
code (FR-19); wrong token -> 401 AUTH_REQUIRED; unknown tenant -> a deliberate refusal JSON (never
leaks another tenant's data, NFR-09); every answer response exposes four stage timings (NFR-14);
an unsupported method or route -> 405/404 with the same error envelope, never an HTML page (FR-23).
"""

from __future__ import annotations

import os
import time
from http.server import ThreadingHTTPServer

from .admission import Admission
from .cache import AnswerCache
from .config import SETTINGS, Settings
from .handler import make_handler
from .retrieval import HybridIndex
from .store import Store

__all__ = ["ServerState", "make_handler", "make_server", "serve"]


class ServerState:
    """One shared read store + one shared hybrid index; per-tenant views cache inside."""

    def __init__(self, db_path: str, settings: Settings = SETTINGS) -> None:
        self.settings = settings
        self.store = Store(db_path, settings=settings)
        self.index = HybridIndex(self.store, settings=settings)
        # M23: the bound that turns saturation into a fast 503 instead of a slow queue
        self.admission = Admission(
            limit=settings.max_inflight,
            queue_limit=settings.max_waiting,
            wait_ms=settings.admission_wait_ms,
        )
        # M25: exact answers for this process; a hit skips retrieval, the gate and extraction
        self.cache = AnswerCache(settings.cache_size)
        self._reloaded: dict[str, float] = {}
        # M29: what the reload path costs, measured where it runs. Three numbers, because the
        # path has two different jobs: a cheap signature check (paid about once per interval per
        # busy tenant) and an actual refresh, which is the only part that grows with the ingest.
        self.reload_count = 0
        self.reload_ms_total = 0.0
        self.reload_samples: list[float] = []
        self.reload_outcomes: dict[str, int] = {}

    def reload(self, tenant_id: str) -> str:
        """Notice an ingest done by another process, at most once per `reload_ms` (M26).

        The gate is a clock, not a counter: a busy tenant must not pay a store query per
        request, and a quiet one must still pick up the new document within the interval.
        """
        if not self.settings.reload_ms:
            return "off"
        now = time.monotonic()
        if now - self._reloaded.get(tenant_id, 0.0) < self.settings.reload_ms / 1000.0:
            return "throttled"
        self._reloaded[tenant_id] = now
        started = time.perf_counter()
        outcome = self.index.refresh(tenant_id)
        elapsed = (time.perf_counter() - started) * 1000.0
        self.reload_count += 1
        self.reload_ms_total += elapsed
        self.reload_outcomes[outcome] = self.reload_outcomes.get(outcome, 0) + 1
        if len(self.reload_samples) < RELOAD_SAMPLES:
            self.reload_samples.append(elapsed)
        return outcome

    def reload_stats(self) -> dict:
        """The reload path's own cost, so a tail can be attributed rather than guessed (M29)."""
        samples = sorted(self.reload_samples)

        def percentile(share: float) -> float | None:
            if not samples:
                return None
            index = min(len(samples) - 1, int(share / 100.0 * len(samples)))
            return round(samples[index], 3)

        return {
            "count": self.reload_count,
            "sampled": len(samples),
            "total_ms": round(self.reload_ms_total, 3),
            "p50_ms": percentile(50.0),
            "p95_ms": percentile(95.0),
            "max_ms": round(samples[-1], 3) if samples else None,
            "outcomes": dict(self.reload_outcomes),
        }


# The stdlib default listen backlog is 5, which drops a burst of connections outright.
# The M10 load curve measured that as 7 failures at 50 users, so the queue is sized for
# the operating point the project claims instead of leaving a socket default in charge.
LISTEN_BACKLOG = 128
# A long soak reloads a few dozen times; a busy tenant could reload far more often, so the
# percentile sample is bounded and the count is not. The mean is always exact.
RELOAD_SAMPLES = 1000


def make_server(address: tuple[str, int], state: ServerState) -> ThreadingHTTPServer:
    """One constructor for the production server and the bench harness, so they match."""
    server = ThreadingHTTPServer(address, make_handler(state))
    server.request_queue_size = LISTEN_BACKLOG
    return server


def serve(db: str | None = None, host: str | None = None, port: int | None = None) -> int:
    """Blocking serve loop; SIGINT/interrupt stops it (single-node ops, no daemon)."""
    db_path = db or os.environ.get("ATLAS_DB", SETTINGS.default_db_path)
    state = ServerState(db_path)
    address = (host or SETTINGS.serve_host, port or SETTINGS.serve_port)
    server = make_server(address, state)
    print(f"[atlas] serving {db_path} on http://{address[0]}:{address[1]} "
          f"(auth {'on' if state.settings.api_token else 'off: set ATLAS_API_TOKEN'})")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("[atlas] shutting down")
    finally:
        state.store.close()
        server.server_close()
    return 0
