# Soak — 10 minutes at 10 users (M10)

Command: `python -m bench.sweep --soak-seconds 600 --users 10 --writer-seconds 20` · commit `37678d1`

| metric | value |
|---|---|
| duration | 600 s |
| requests | 536115 |
| served (200) | 536115 |
| shed (503 OVERLOADED) | 0 (0.00%, p95 0.00 ms) |
| failures | 0 (0.000%) |
| p50 ms | 9.15 |
| p95 ms (served) | 23.70 |
| p99 ms | 34.23 |
| sustained rps | 893.5 |
| load model | no think time (saturation model: the same users, offering far more load) |
| served corpus | 18 -> 48 documents (124 chunks) |
| reload calls | 600 ({'missing': 1, 'none': 570, 'extend': 29}) |
| reload total | 1792.60 ms (0.003344 ms per request) |
| reload p50 / p95 / max | 1.46 / 8.58 / 52.21 ms |
| concurrent writer | one document every 20 s, into tenant `campus` |
| documents appended | 30 |
| writer's tenant | 18 -> 48 documents (124 chunks) |
| ingest p95 | 31.07 ms |
| ingest errors | 0 |

## Interpretation

- A concurrent writer appended 30 generated documents (one every 20 s) through the production ingest path into the tenant being served, taking the corpus 18 -> 48 documents (124 chunks) while every request above was answered.
- The write side never took the read side with it: ingest p95 31.07 ms, 0 errors. Both connections run WAL (M26), so a write appends beside a reader instead of taking the database lock.
- The reload path is measured, not inferred: the server times every `refresh()` it performs, and the table above carries the count, total and tail of those calls. `## Where the tail comes from` puts the view-size arm and the control beside this run, so the reload is a line item rather than a remainder.
- The appended documents are generated text over a 300-word vocabulary: they exist to be ingested, not to be answered. This run is a claim about serving through an ingest, not about answer quality.
- p95 after 10 minutes: 23.70 ms, +5.51 ms against the short-run p95 (18.19 ms). A leak or an unbounded cache would show here as growth.
- The reload path cost 1792.60 ms in 600 calls (0.003344 ms per request), outcomes {'missing': 1, 'none': 570, 'extend': 29}. The 571 calls that found an unchanged corpus version are cheap per call (p50 1.46 ms) but they are the frequent ones, so they dominate that total; only 29 of the calls refreshed anything.
- NFR-01 is written at 10 users, and this run is exactly that load held for 10 minutes with no pause between requests: p95 23.70 ms against a 50 ms target at 10 concurrent users. Nothing was shed, so every request offered was answered inside it.
- Nothing was shed in this run, so the admission bound never became the binding constraint; it is a ceiling, not a throttle on normal traffic.
- Unlike the burst, this run cannot borrow credit from an idle moment: the threshold holds under sustained load.
- Error rate 0.000% over 536115 requests; these are
  dropped connections, and none of them changed an answer's content.
- The per-tenant view is the only thing that grows with traffic, and it is keyed by
  tenant, so this run also checks that repeated requests do not accumulate per request.
- The answer cache is off here (`ATLAS_CACHE_SIZE=0`), so this is the sustained cost of answering every request rather than the cost of repeating one; `results/cache.md` is where a warm cache is measured.

## Where the tail comes from

| arm | what changes under the run | p95 ms | vs short curve |
|---|---|---|---|
| short curve | nothing | 18.19 | - |
| control | writes into a tenant nobody reads | 28.18 | +9.98 |
| writer | writes into the served tenant, 18 -> 48 documents | 23.70 | +5.51 |
| pre-grown | 48 documents held still, nothing else running | 24.67 | +6.48 |

- **The reload path is measured, not subtracted.** The writer soak made 600 reload calls for 1793 ms in total ({'missing': 1, 'none': 570, 'extend': 29}), p50 1.46 ms and p95 8.58 ms per call, of which 29 found a changed corpus and did any work. Spread over 536115 requests that is 0.0033 ms per request: far too small to move a p95, and it does not have to be - the requests that trigger a reload are a few dozen out of half a million.
- **The ordering the split needs did not hold in this session.** Control 28.18 ms (+9.98 over the short curve), writer 23.70 (+5.51), pre-grown 24.67 (+6.48): a spread of 4.48 ms across the three arms.
- The M29 session read the same arms at 21.62, 23.96 and 25.24 ms, so between the two sessions the control moved +6.55 ms while writer and pre-grown moved -0.26 and -0.57. The control's throughput here is 761 rps against the writer's 894 (15% lower), which is that arm meeting a slower host for its ten minutes rather than 9.98 ms of work, and it is larger than the effect the arm was built to measure. Both sessions are on one desktop under its own load (L13).
- **So no split is claimed.** The control reads +9.98 ms over the short curve where M29 read +3.43, and it lands above the writer rather than below it, so writing and view size are not separable at the noise level: the writer soak's +5.51 ms is a bracket set by arms that differ by 4.48 ms, not a decomposition of it. What is measured independently of the arms is the reload path - 0.0033 ms per request over 600 counted calls, with its own total, p50 and tail above. M28's +3.06 ms "reload and corpus growth" therefore still has one unattributed part: the reload is counted and two orders of magnitude too small to be it; what is left is ingestion plus a larger view, which these arms cannot divide.

- The other load model is committed beside this one: `results/soak-interactive.json` reports p95 9.89 ms at 10 users with 100 ms think time. The two answer different questions - headroom versus user traffic - and neither replaces the other.

