# Soak — 15 minutes at 10 users (M10)

Command: `python -m bench.sweep --soak-seconds 900 --users 10 --think-ms 100` · commit `5398f23`

| metric | value |
|---|---|
| duration | 900 s |
| requests | 84925 |
| served (200) | 84925 |
| shed (503 OVERLOADED) | 0 (0.00%, p95 0.00 ms) |
| failures | 0 (0.000%) |
| p50 ms | 5.01 |
| p95 ms (served) | 9.89 |
| p99 ms | 12.72 |
| sustained rps | 94.4 |
| load model | 100 ms think time per user (interactive model) |

## Interpretation

- p95 after 15 minutes: 9.89 ms, -8.30 ms against the short-run p95 (18.19 ms). A leak or an unbounded cache would show here as growth.
- The reload path was not instrumented when this run was taken, so its cost here is unknown rather than zero; `results/soak-writer.md` and `results/soak-pregrown.md` carry the counters.
- NFR-01 is written at 10 users, and this run holds that load with 100 ms of think time per user: p95 9.89 ms against a 50 ms target at 10 concurrent users, and 0 requests were shed, so read strictly NFR-01 passes.
- Nothing was shed in this run, so the admission bound never became the binding constraint; it is a ceiling, not a throttle on normal traffic.
- Unlike the burst, this run cannot borrow credit from an idle moment: the threshold holds under sustained load.
- Error rate 0.000% over 84925 requests; these are
  dropped connections, and none of them changed an answer's content.
- The per-tenant view is the only thing that grows with traffic, and it is keyed by
  tenant, so this run also checks that repeated requests do not accumulate per request.
- The answer cache is off here (`ATLAS_CACHE_SIZE=0`), so this is the sustained cost of answering every request rather than the cost of repeating one; `results/cache.md` is where a warm cache is measured.

## Where the tail comes from

| arm | what changes under the run | p95 ms | vs short curve |
|---|---|---|---|
| short curve | nothing | 18.19 | - |
| control | writes into a tenant nobody reads | 21.62 | +3.43 |
| writer | writes into the served tenant, 18 -> 48 documents | 23.95 | +5.76 |
| pre-grown | 48 documents held still, nothing else running | 25.24 | +7.05 |

- **The reload path is measured, not subtracted.** The writer soak made 600 reload calls for 3031 ms in total ({'missing': 1, 'none': 570, 'extend': 29}), p50 3.45 ms and p95 13.35 ms per call, of which 29 found a changed corpus and did any work. Spread over 511751 requests that is 0.0059 ms per request: far too small to move a p95, and it does not have to be - the requests that trigger a reload are a few dozen out of half a million.
- **The bigger served view costs more than the reload does.** The pre-grown arm holds the corpus the writer finishes with, still, and reads 25.24 ms (+7.05 over the short curve). The writer soak reads 23.95 ms, between the control (21.62) and that arm - which is what a view growing from 18 to 48 documents during the run should look like, because its average size is half the final one.
- **So:** of the writer soak's +5.76 ms, about +3.43 ms is writing beside the service and the rest is the larger corpus it is serving; the reload contributes 0.0059 ms per request. M28 reported +3.06 ms as "the reload and the corpus growth" together; these arms separate them, and the reload is not the larger part.

- The other load model is committed beside this one: `results/soak.json` reports p95 22.88 ms at 10 users with no think time (saturation). The two answer different questions - headroom versus user traffic - and neither replaces the other.

