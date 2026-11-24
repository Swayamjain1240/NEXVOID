# Worker processes — 1, 2 and 4 behind one port (M24, NFR-01)

Command: `python -m bench.workers` · commit `74e388d` · platform: Windows-11-10.0.26300-SP0

10 client processes, 100 requests each, one listening socket shared by the workers, real `GET /v1/ask` through the production handler.

| workers | requests | served | p50 ms | p95 ms | rps | pids observed |
|---|---|---|---|---|---|---|
| 1 | 1000 | 1000 | 10.59 | 24.96 | 378.1 | 1 |
| 2 | 1000 | 1000 | 5.18 | 11.08 | 436.9 | 2 |
| 4 | 1000 | 1000 | 2.50 | 5.10 | 587.3 | 4 |

Throughput 378 rps at one worker to 587 rps at 4 (1.55×), p95 24.96 → 5.10 ms.

## Interpretation

- Latency and throughput scale differently here, and the load explains it: at a fixed 10 users offering one request at a time, throughput cannot exceed 10 / service time, so the *client* caps it long before the workers do. p95 24.96 → 5.10 ms is the real result; more workers mainly remove queueing, not work.
- Each worker owns its own `Store` connection, index and tenant views, so the first requests to a new worker pay the warm-up. The script warms every worker before measuring; a cold pool would look worse than this.
- `/v1/metrics` answers from whichever worker accepted the connection. With more than one worker its counters describe one process, not the pool, and `pid` in `/v1/healthz` is how you know which (L13). No number here is an average across processes pretending to be one system.
- The client is the other limit: ten processes on one machine contend for the same cores the server uses, so the curve measures this laptop's ability to both offer and serve load, not a datacentre.

- `--workers N` is the operator surface (`atlas.cli serve --workers N`); the parent supervises and restarts a child that dies.

