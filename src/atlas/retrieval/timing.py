"""Stage timings for one request (M22, NFR-14).

Timers write into a dict the *caller* owns, never into the tenant view: a shared
"last timings" attribute would be a race under the threaded server, and a racy
observability field is worse than no field at all.

`None` means nobody is timing (the trainer, the ablation arms), so a caller that does
not care pays one branch per stage and nothing else.
"""

from __future__ import annotations

from time import perf_counter


def stamp(timings: dict[str, float] | None, key: str, started: float) -> None:
    """Record `key` as milliseconds since `started`; a no-op when `timings` is None."""
    if timings is not None:
        timings[key] = round((perf_counter() - started) * 1000.0, 4)


def start() -> float:
    """One named entry point, so instrumented and uninstrumented paths read the same."""
    return perf_counter()
