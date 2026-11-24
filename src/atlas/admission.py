"""Admission control: a bounded queue instead of an unbounded one (M23, NFR-01).

The soak failed at p95 58 ms because ten unpaced clients offer more work than one process can
serve, and the excess waited in an unbounded queue. Response time is queue wait plus service
time, so once utilisation approaches 1 the wait dominates and *every* request gets slower -
including the ones the server could have answered quickly.

This module puts a number on both ends of that queue:

- at most `limit` requests are served at once;
- at most `queue_limit` more may wait, and each waits at most `wait_ms`;
- anything beyond that is rejected **immediately** with `503 OVERLOADED`.

Rejecting fast is the point. A shed request frees its slot in microseconds and the client can
retry; a queued request holds a thread and makes the p95 of the requests that *are* served
worse. The counters are exposed on `/v1/metrics` so shedding is visible rather than inferred
from a latency curve.
"""

from __future__ import annotations

from threading import BoundedSemaphore, Lock


class Admission:
    """Bounded concurrency with a wait deadline; `acquire` never blocks longer than `wait_ms`."""

    def __init__(self, *, limit: int, queue_limit: int, wait_ms: float) -> None:
        if limit < 1:
            raise ValueError("limit must be at least 1")
        self._slots = BoundedSemaphore(limit)
        self._lock = Lock()
        self._limit = limit
        self._queue_limit = max(0, queue_limit)
        self._wait_s = max(0.0, wait_ms) / 1000.0
        self._wait_ms = wait_ms
        self._inflight = 0
        self._waiting = 0
        self._admitted = 0
        self._rejected = 0

    def acquire(self) -> bool:
        """Take a slot, waiting at most `wait_ms`; False means "shed this request".

        A free slot is taken without touching the queue at all: the queue bound is a limit on
        *waiting*, so an idle server must never reject because the queue is configured to be
        empty.
        """
        if self._slots.acquire(blocking=False):
            self._grant()
            return True
        with self._lock:
            if self._waiting >= self._queue_limit or self._wait_s <= 0.0:
                self._rejected += 1
                return False
            self._waiting += 1
        try:
            granted = self._slots.acquire(timeout=self._wait_s)
        finally:
            with self._lock:
                self._waiting -= 1
        if not granted:
            with self._lock:
                self._rejected += 1
            return False
        self._grant()
        return True

    def _grant(self) -> None:
        with self._lock:
            self._inflight += 1
            self._admitted += 1

    def release(self) -> None:
        """Give the slot back; called from the request's `finally`, never conditionally."""
        with self._lock:
            self._inflight -= 1
        self._slots.release()

    def snapshot(self) -> dict[str, float | int]:
        """The JSON block `/v1/metrics` publishes beside the route counters."""
        with self._lock:
            served = self._admitted
            rejected = self._rejected
            return {
                "limit": self._limit,
                "queue_limit": self._queue_limit,
                "wait_ms": self._wait_ms,
                "inflight": self._inflight,
                "waiting": self._waiting,
                "admitted": served,
                "rejected": rejected,
                "reject_rate": (
                    round(rejected / (served + rejected), 4) if served + rejected else 0.0
                ),
            }
