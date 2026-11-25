# Load curve — 1, 10 and 50 concurrent users (M10)

Command: `python -m bench.sweep` · commit `5398f23` · platform: Windows-11-10.0.26300-SP0

| users | requests | served | shed | failures | p50 ms | p95 ms | p99 ms | rps |
|---|---|---|---|---|---|---|---|---|
| 1 | 100 | 100 | 0 | 0 | 1.08 | 2.12 | 3.01 | 113.4 |
| 10 | 1000 | 1000 | 0 | 0 | 7.23 | 18.19 | 28.99 | 490.2 |
| 50 | 5000 | 5000 | 0 | 0 | 16.82 | 66.24 | 106.08 | 544.5 |

NFR-01 (p95 <= 50 ms at 10 users): **PASS** (p95 18.19 ms), with 0 of 1000 requests shed as 503 OVERLOADED (M23 admission control).

Method: real `ThreadingHTTPServer` over a temporary database, one client PROCESS per user with a persistent keep-alive connection, 100 requests per user per level.
The curve is the point: a single number cannot show where the knee is.

The 30-minute soak holds this same load (10 users) and reads p95 22.88 ms, inside the 50 ms target. It is the stricter of the two readings because it cannot borrow credit from an idle moment.

## Interpretation

- p95 grows 31.2x from 1 to 50 users (2.1 -> 66.2 ms) while throughput reaches 544 rps.
  The server is not saturated at 50: the cost is queueing on one process, which is the documented deployment shape rather than a defect.
- Failures at every level: 0. A failure here is a
  dropped connection, not a refused answer: refusals are HTTP 200 by design.
- Two harness findings from getting this curve right, both measured: the stdlib listen
  backlog of 5 dropped 7 connections at 50 users until it was raised to 128, and logging
  every request into a captured pipe read p95 55.39 ms at 10 users (a FAIL) until the log
  went to a file, as a deployed process writes it (46.52 ms, PASS). The logger is in the
  tail path; that is a result about the service, not an excuse for the number.
- Shed at every level: 0 of 6100 requests, all of them 503 OVERLOADED from the M23 bound rather than dropped connections. The 10-user level is the NFR-01 operating point and sits below the bound, so the target is measured without shedding.
- The answer cache is off here (`ATLAS_CACHE_SIZE=0`): 100 requests per user over 24 questions would otherwise be a cache benchmark, not a retrieval one. `results/cache.md` measures the cache, with its own repeat mix.
- One process, one port, no container: this curve is the whole capacity story for the
  deployment the project claims, and it says nothing about horizontal scaling.

