"""The HTTP handler behind `server.py` (FR-20..FR-23, NFR-01, NFR-09, NFR-14).

Split out so each file keeps one responsibility inside the 200-line ceiling: this module answers
requests, `server.py` builds and runs the socket. The route table and the contract notes live in
`server.py`'s docstring, which is the published surface.
"""

from __future__ import annotations

import json
import time
from http.server import BaseHTTPRequestHandler
from pathlib import Path
from urllib.parse import urlparse

from . import api
from .answer import answer_query
from .interfaces import AtlasError, InputError
from .metrics import METRICS
from .obs import redact

ROOT = Path(__file__).resolve().parents[2]  # src/atlas/handler.py -> model 1/
DEMO_PAGE = ROOT / "web" / "demo.html"
_NO_ROUTE = "unknown route; see GET /v1/healthz"


def make_handler(state) -> type:
    """Binds the shared state once; the handler class itself stays stateless."""

    class AtlasHandler(BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.1"
        disable_nagle_algorithm = True  # M5 load-test finding: kills the ~40ms tail stalls

        def do_GET(self) -> None:
            parsed = urlparse(self.path)
            if parsed.path == "/":
                self._dispatch("GET /", self._demo)
            elif parsed.path in ("/health", "/v1/healthz"):
                self._dispatch(f"GET {parsed.path}", self._health)
            elif parsed.path == "/v1/ask":
                self._dispatch("GET /v1/ask", lambda: self._ask(parsed))
            elif parsed.path == "/v1/metrics":
                self._dispatch("GET /v1/metrics", self._metrics)
            else:
                self._dispatch("GET other", lambda: self._error("NOT_FOUND", 404, _NO_ROUTE))

        def do_POST(self) -> None:
            parsed = urlparse(self.path)
            if parsed.path == "/v1/ask":
                self._dispatch("POST /v1/ask", self._ask_post)
            else:
                self._dispatch("POST other", lambda: self._error("NOT_FOUND", 404, _NO_ROUTE))

        def do_PUT(self) -> None:
            self._dispatch("other method", self._not_allowed)

        do_DELETE = do_PATCH = do_PUT  # one answer for every method the API does not offer

        def _dispatch(self, route: str, handler) -> None:
            """Time one request, catch what escapes, and record it exactly once."""
            self._started = time.perf_counter()
            self._status_sent = 500
            try:
                handler()
            except Exception as error:  # FR-23: a code, never a traceback over the wire
                self._error("SERVER_ERROR", 500, error.__class__.__name__)
            finally:
                METRICS.record(
                    route, self._status_sent, (time.perf_counter() - self._started) * 1000.0
                )

        def _ask(self, parsed) -> None:
            if self._authorised():
                self._parse_and_answer(lambda: api.parse_get_query(parsed.query))

        def _ask_post(self) -> None:
            if not self._authorised():
                return
            try:
                length = int(self.headers.get("Content-Length") or 0)
            except ValueError:
                self._error("INPUT_INVALID", 400, "Content-Length must be an integer")
                return
            if length > api.MAX_BODY_BYTES:
                self._error("INPUT_INVALID", 400, f"body exceeds {api.MAX_BODY_BYTES} bytes")
                return
            body = self.rfile.read(length) if length else b""
            self._parse_and_answer(lambda: api.parse_post_body(body))

        def _parse_and_answer(self, parse) -> None:
            """A malformed request is a 400 with a code, not a 500 from the dispatcher."""
            try:
                request = parse()
            except InputError as error:
                self._error("INPUT_INVALID", 400, str(error))
                return
            self._answer(request)

        def _answer(self, request: dict) -> None:
            tenant = self.headers.get("X-Tenant", state.settings.default_tenant)
            try:
                answer = answer_query(
                    state.index,
                    request["query"],
                    tenant_id=tenant,
                    settings=state.settings,
                    mode=request["mode"],
                )
            except InputError as error:
                self._error("INPUT_INVALID", 400, str(error))
                return
            except AtlasError as error:  # never leak internals, always a code
                self._error("SERVER_ERROR", 500, error.__class__.__name__)
                return
            self._json(200, api.answer_payload(answer, self._started))

        def _authorised(self) -> bool:
            token = self.headers.get("Authorization", "")
            if state.settings.api_token and token != f"Bearer {state.settings.api_token}":
                self._error("AUTH_REQUIRED", 401, "missing or wrong bearer token")
                return False
            return True

        def _metrics(self) -> None:
            self._json(200, METRICS.snapshot())

        def _health(self) -> None:
            # theta and the encoder go out with liveness because the UI has to draw the
            # refusal line at the threshold this process is actually running, not a
            # number copied into the page from a report.
            self._json(200, {
                "status": "ok",
                "tenants_served": len(state.index._views),
                "embedder": state.settings.embedder_kind,
                "theta": state.settings.theta,
                "mode": "hybrid",
            })

        def _demo(self) -> None:
            if not DEMO_PAGE.exists():
                self._error("NOT_FOUND", 404, "demo page missing from web/demo.html")
                return
            body = DEMO_PAGE.read_bytes()
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def _not_allowed(self) -> None:
            self._json(405, api.error_payload(
                "METHOD_NOT_ALLOWED", "only GET and POST are supported on this API"
            ))

        def _error(self, code: str, status: int, message: str) -> None:
            self._json(status, api.error_payload(code, message))

        def _json(self, status: int, payload: dict) -> None:
            self._status_sent = status
            body = json.dumps(payload).encode("utf-8")
            self.send_response(status)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, fmt: str, *args) -> None:
            # NFR-11: the request line carries the user's question, so it is redacted here
            print(redact(f"[atlas] {self.client_address[0]} {fmt % args}"))

    return AtlasHandler
