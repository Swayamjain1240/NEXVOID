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


# The stdlib default listen backlog is 5, which drops a burst of connections outright.
# The M10 load curve measured that as 7 failures at 50 users, so the queue is sized for
# the operating point the project claims instead of leaving a socket default in charge.
LISTEN_BACKLOG = 128


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
