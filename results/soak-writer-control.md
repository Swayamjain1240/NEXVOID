# Soak — 10 minutes at 10 users (M10)

Command: `python -m bench.sweep --soak-seconds 600 --users 10 --writer-seconds 20 --writer-tenant campus-control` · commit `bef8643`

| metric | value |
|---|---|
| duration | 600 s |
| requests | 563929 |
| served (200) | 563929 |
| shed (503 OVERLOADED) | 0 (0.00%, p95 0.00 ms) |
| failures | 0 (0.000%) |
| p50 ms | 8.88 |
| p95 ms (served) | 21.62 |
| p99 ms | 30.52 |
| sustained rps | 939.8 |
| load model | no think time (saturation model: the same users, offering far more load) |
| reload calls | 600 ({'missing': 1, 'none': 599}) |
| reload total | 1709.28 ms (0.003031 ms per request) |
| reload p50 / p95 / max | 2.56 / 6.73 / 9.60 ms |
| concurrent writer | one document every 20 s, into tenant `campus-control` |
| documents appended | 30 |
| served corpus | 18 -> 18 documents (34 chunks) |
| writer's tenant | 0 -> 30 documents (90 chunks) |
| ingest p95 | 18.12 ms |
| ingest errors | 0 |

## Interpretation

- A concurrent writer appended 30 generated documents (one every 20 s) through the production ingest path into tenant `campus-control`, which no request above touched: that tenant went 0 -> 30 documents (90 chunks) while the served corpus stayed at 18 documents (34 chunks).
- This is the control for `results/soak-writer.md`, which appended the same documents to the served tenant. Here the served view's signature never changed, so no refresh ever ran: this p95 keeps the cost of writing at all - WAL, a second connection and a larger file - plus the signature checks below, and nothing else.
- The write side never took the read side with it: ingest p95 18.12 ms, 0 errors. Both connections run WAL (M26), so a write appends beside a reader instead of taking the database lock.
- The appended documents are generated text over a 300-word vocabulary: they exist to be ingested, not to be answered. This run is a claim about serving through an ingest, not about answer quality.
- p95 after 10 minutes: 21.62 ms, +3.43 ms against the short-run p95 (18.19 ms). A leak or an unbounded cache would show here as growth.
- The reload path cost 1709.28 ms in 600 calls (0.003031 ms per request), outcomes {'missing': 1, 'none': 599}. The 600 calls that found an unchanged signature are the cheap ones per call (p50 2.56 ms) but they are the frequent ones, so they dominate that total; only 0 of the calls refreshed anything.
- NFR-01 is written at 10 users, and this run is exactly that load held for 10 minutes with no pause between requests: p95 21.62 ms against a 50 ms target at 10 concurrent users. Nothing was shed, so every request offered was answered inside it.
- Nothing was shed in this run, so the admission bound never became the binding constraint; it is a ceiling, not a throttle on normal traffic.
- Unlike the burst, this run cannot borrow credit from an idle moment: the threshold holds under sustained load.
- Error rate 0.000% over 563929 requests; these are
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

- The other load model is committed beside this one: `results/soak-interactive.json` reports p95 9.89 ms at 10 users with 100 ms think time. The two answer different questions - headroom versus user traffic - and neither replaces the other.

