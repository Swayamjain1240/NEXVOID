# Load curve — 1, 10 and 50 concurrent users (M10) — same code, same machine, another session (2026-11-25)

Command: `python -m bench.sweep` · commit `03eccbf` · platform: Windows-11-10.0.26300-SP0

| users | requests | served | shed | failures | p50 ms | p95 ms | p99 ms | rps |
|---|---|---|---|---|---|---|---|---|
| 1 | 100 | 100 | 0 | 0 | 1.08 | 2.03 | 3.89 | 126.6 |
| 10 | 1000 | 1000 | 0 | 0 | 7.18 | 19.09 | 27.18 | 540.0 |
| 50 | 5000 | 4963 | 37 | 0 | 21.35 | 69.37 | 101.89 | 602.7 |

NFR-01 (p95 <= 50 ms at 10 users): **PASS** (p95 19.09 ms), with 0 of 1000 requests shed as 503 OVERLOADED (M23 admission control).

Method: real `ThreadingHTTPServer` over a temporary database, one client PROCESS per user with a persistent keep-alive connection, 100 requests per user per level.
The curve is the point: a single number cannot show where the knee is.


## Interpretation

- p95 grows 34.2x from 1 to 50 users (2.0 -> 69.4 ms) while throughput reaches 603 rps.
  At 50 the bound is reached and 37 requests are shed, so the tail above it is the socket queue, not the answer path.
- Failures at every level: 0. A failure here is a
  dropped connection, not a refused answer: refusals are HTTP 200 by design.
- Two harness findings from getting this curve right, both measured: the stdlib listen
  backlog of 5 dropped 7 connections at 50 users until it was raised to 128, and logging
  every request into a captured pipe read p95 55.39 ms at 10 users (a FAIL) until the log
  went to a file, as a deployed process writes it (46.52 ms, PASS). The logger is in the
  tail path; that is a result about the service, not an excuse for the number.
- Shed at every level: 37 of 6100 requests, all of them 503 OVERLOADED from the M23 bound rather than dropped connections. The 10-user level is the NFR-01 operating point and sits below the bound, so the target is measured without shedding.
- The answer cache is off here (`ATLAS_CACHE_SIZE=0`): 100 requests per user over 24 questions would otherwise be a cache benchmark, not a retrieval one. `results/cache.md` measures the cache, with its own repeat mix.
- One process, one port, no container: this curve is the whole capacity story for the
  deployment the project claims, and it says nothing about horizontal scaling.

