"""Payloads for the two routes that describe the process (FR-20, FR-22, M24).

Split out of `handler.py` when the admission block pushed that module over the 200-line
ceiling: the handler answers requests, these two functions decide what "how is this process
doing" looks like, and neither needs the other's internals.

Both are deliberately cheap. They are the endpoints an operator reaches for *during* an
overload, so they are never shed and never touch the retrieval path.
"""

from __future__ import annotations

import os

from .metrics import METRICS


def health_payload(state) -> dict:
    """Liveness plus the two numbers the demo page needs to draw its own threshold."""
    return {
        "status": "ok",
        # M24: with worker processes behind one port, which process answered is part of
        # what liveness can tell you.
        "pid": os.getpid(),
        "tenants_served": len(state.index._views),
        "embedder": state.settings.embedder_kind,
        "theta": state.settings.theta,
        "mode": "hybrid",
    }


def metrics_payload(state) -> dict:
    """Route counters plus capacity: a 503 is explainable after the fact (M23)."""
    payload = METRICS.snapshot()
    payload["admission"] = state.admission.snapshot()
    # M25: hits, misses and the rate they imply, so a cache claim is checkable from outside
    payload["cache"] = state.cache.snapshot()
    # M29: the reload path's count, total and tail, so an ingest's cost is not inferred from a
    # latency curve that cannot tell a bigger corpus from a rebuild
    payload["reload"] = state.reload_stats()
    return payload
