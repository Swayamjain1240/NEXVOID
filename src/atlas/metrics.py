"""Process-wide request counters and latency summary (FR-20, NFR-14).

Deliberately tiny and stdlib-only: what the SRS promises under `GET /metrics` is a documented
snapshot of what this process has served, not an observability platform. Two rules keep it safe
to expose:

- **No request content is stored.** A route label (`GET /v1/ask`) and a status code, never a
  query, a tenant or a trace id - the same reason the request log is redacted (NFR-11).
- **Bounded memory.** Latencies live in a fixed-size window per route, so a process that runs for
  a week reports the last 1000 requests rather than growing until it dies.

The snapshot is per process by design (NFR-03: one node, one process). With more than one worker
these numbers would be partial, and the report says so rather than pretending otherwise.
"""

from __future__ import annotations

import time
from collections import deque
from threading import Lock


def _summary(values: list[float]) -> dict:
    """count / mean / p50 / p95 / max for one route's window, rounded for JSON."""
    if not values:
        return {"count": 0, "mean_ms": 0.0, "p50_ms": 0.0, "p95_ms": 0.0, "max_ms": 0.0}
    ordered = sorted(values)
    p50 = ordered[int(0.50 * (len(ordered) - 1))]
    p95 = ordered[int(0.95 * (len(ordered) - 1))]
    return {
        "count": len(ordered),
        "mean_ms": round(sum(ordered) / len(ordered), 3),
        "p50_ms": round(p50, 3),
        "p95_ms": round(p95, 3),
        "max_ms": round(ordered[-1], 3),
    }


class Metrics:
    """Thread-safe counters for one process; `ThreadingHTTPServer` calls this concurrently."""

    def __init__(self, window: int = 1000) -> None:
        self._lock = Lock()
        self._started = time.time()
        self._counts: dict[str, dict[str, int]] = {}
        self._latencies: dict[str, deque[float]] = {}
        self._window = window

    def record(self, route: str, status: int, duration_ms: float) -> None:
        """One completed request: route label, status class and wall time."""
        with self._lock:
            entry = self._counts.setdefault(route, {"count": 0, "errors": 0})
            entry["count"] += 1
            if status >= 400:
                entry["errors"] += 1
            self._latencies.setdefault(route, deque(maxlen=self._window)).append(duration_ms)

    def snapshot(self) -> dict:
        """The JSON body of `GET /v1/metrics`: totals first, then per route."""
        with self._lock:
            routes = {
                route: {**entry, "latency": _summary(list(self._latencies.get(route, ())))}
                for route, entry in sorted(self._counts.items())
            }
            requests = sum(entry["count"] for entry in self._counts.values())
            errors = sum(entry["errors"] for entry in self._counts.values())
            uptime = round(time.time() - self._started, 1)
        return {
            "uptime_s": uptime,
            "requests": requests,
            "errors": errors,
            "window": self._window,
            "routes": routes,
        }

    def reset(self) -> None:
        """Tests and the contract probe start from a known zero."""
        with self._lock:
            self._counts.clear()
            self._latencies.clear()
            self._started = time.time()


METRICS = Metrics()
