"""Exact answer cache (M25): the same question, tenant, mode and date, without the work.

Three traps come before the speedup, and the key answers all three by construction:

* **the tenant.** Without it in the key, a hit would be a cross-tenant data leak - a speedup
  that reads someone else's policy. It is part of the lookup key, never a post-filter.
* **the date** (M21). "What is the attendance bar?" asked about 2024 and about 2026 is the
  same string; without `as_of` the first answer would be served for the second.
* **the corpus.** `corpus_hash` fingerprints the tenant's chunks, so a changed document makes
  every old key unreachable instead of serving a superseded rule.

The cache is bounded and per process (NFR-03): with N workers there are N caches and N
independent hit rates, which `results/cache.md` states rather than averaging away. A degraded
answer is never stored - it is the fallback for a failed extraction, and caching it would keep
serving the fallback after the generator recovered.
"""

from __future__ import annotations

import dataclasses
import time
import uuid
from collections import OrderedDict
from dataclasses import dataclass
from threading import Lock

from .answer import answer_query
from .interfaces import Answer


@dataclass(frozen=True)
class CacheKey:
    """Everything an answer depends on. A field missing here is a wrong answer, not a miss."""

    tenant: str
    query: str
    mode: str
    as_of: str
    corpus_hash: str


class AnswerCache:
    """Bounded LRU of finished answers, with the counters `/v1/metrics` publishes."""

    def __init__(self, capacity: int = 256) -> None:
        self._capacity = max(0, capacity)  # 0 disables the cache without a second flag
        self._lock = Lock()
        self._entries: OrderedDict[CacheKey, Answer] = OrderedDict()
        self._hits = 0
        self._misses = 0
        self._evictions = 0

    def get(self, key: CacheKey) -> Answer | None:
        with self._lock:
            answer = self._entries.get(key)
            if answer is None:
                self._misses += 1
                return None
            self._entries.move_to_end(key)
            self._hits += 1
            return answer

    def put(self, key: CacheKey, answer: Answer) -> None:
        if not self._capacity:
            return
        with self._lock:
            if key in self._entries:
                self._entries.move_to_end(key)
            self._entries[key] = answer
            while len(self._entries) > self._capacity:
                self._entries.popitem(last=False)
                self._evictions += 1

    def snapshot(self) -> dict:
        """What `/v1/metrics` reports: the counters, the bound and the rate they imply."""
        with self._lock:
            total = self._hits + self._misses
            return {
                "capacity": self._capacity,
                "size": len(self._entries),
                "hits": self._hits,
                "misses": self._misses,
                "hit_rate": round(self._hits / total, 4) if total else 0.0,
                "evictions": self._evictions,
            }

    def clear(self) -> None:
        """Tests and the benchmark start from a cold cache; counters are left alone."""
        with self._lock:
            self._entries.clear()


def answer_with_cache(state, request: dict, tenant: str) -> Answer:
    """One parsed request: from the cache when the key matches, otherwise from the pipeline."""
    key = CacheKey(
        tenant=tenant,
        query=request["query"],
        mode=request["mode"],
        as_of=request["as_of"],
        corpus_hash=state.index.corpus_hash(tenant),
    )
    cached = state.cache.get(key)
    if cached is not None:
        return _replay(cached)
    answer = answer_query(
        state.index,
        request["query"],
        tenant_id=tenant,
        settings=state.settings,
        mode=request["mode"],
        as_of=request["as_of"],
    )
    if not answer.degraded:
        state.cache.put(key, answer)
    return answer


def _replay(answer: Answer) -> Answer:
    """The cached body, with a fresh trace id and timings that admit no retrieval happened."""
    return dataclasses.replace(
        answer,
        trace_id=uuid.uuid4().hex[:16],
        cache="hit",
        timings_ms={
            "retrieve_ms": 0.0,
            "gate_ms": 0.0,
            "extract_ms": 0.0,
            "total_ms": 0.0,
            "retrieval": {},
        },
    )


def timed_lookup(state, request: dict, tenant: str) -> tuple[Answer, float]:
    """`answer_with_cache` plus the wall time, so a caller can report what a hit costs."""
    started = time.perf_counter()
    answer = answer_with_cache(state, request, tenant)
    return answer, (time.perf_counter() - started) * 1000.0
