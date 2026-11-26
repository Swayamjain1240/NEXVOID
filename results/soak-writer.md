# Soak — 10 minutes at 10 users (M10)

Command: `python -m bench.sweep --soak-seconds 600 --users 10 --writer-seconds 20` · commit `286ad22`

| metric | value |
|---|---|
| duration | 600 s |
| requests | 502740 |
| served (200) | 502740 |
| shed (503 OVERLOADED) | 0 (0.00%, p95 0.00 ms) |
| failures | 0 (0.000%) |
| p50 ms | 9.98 |
| p95 ms (served) | 24.38 |
| p99 ms | 34.55 |
| sustained rps | 837.8 |
| load model | no think time (saturation model: the same users, offering far more load) |
| concurrent writer | one document every 20 s, into tenant `campus` |
| documents appended | 30 |
| served corpus | 18 -> 48 documents (124 chunks) |
| writer's tenant | 18 -> 48 documents (124 chunks) |
| ingest p95 | 33.21 ms |
| ingest errors | 0 |

## Interpretation

- A concurrent writer appended 30 generated documents (one every 20 s) through the production ingest path into the tenant being served, taking the corpus 18 -> 48 documents (124 chunks) while every request above was answered.
- The write side never took the read side with it: ingest p95 33.21 ms, 0 errors. Both connections run WAL (M26), so a write appends beside a reader instead of taking the database lock.
- The reload is inside this number, not beside it: the server notices a changed corpus signature at most once per `ATLAS_RELOAD_MS` (1000 ms), so a request after an append pays to rebuild the affected view. `results/soak-writer-control.md` is the same run with the writes pointing at a tenant nobody reads, which is how much of that tail the reload is.
- The appended documents are generated text over a 300-word vocabulary: they exist to be ingested, not to be answered. This run is a claim about serving through an ingest, not about answer quality.
- p95 after 10 minutes: 24.38 ms, +6.19 ms against the short-run p95 (18.19 ms). A leak or an unbounded cache would show here as growth.
- NFR-01 is written at 10 users, and this run is exactly that load held for 10 minutes with no pause between requests: p95 24.38 ms against a 50 ms target at 10 concurrent users. Nothing was shed, so every request offered was answered inside it.
- Nothing was shed in this run, so the admission bound never became the binding constraint; it is a ceiling, not a throttle on normal traffic.
- Unlike the burst, this run cannot borrow credit from an idle moment: the threshold holds under sustained load.
- Error rate 0.000% over 502740 requests; these are
  dropped connections, and none of them changed an answer's content.
- The per-tenant view is the only thing that grows with traffic, and it is keyed by
  tenant, so this run also checks that repeated requests do not accumulate per request.
- The answer cache is off here (`ATLAS_CACHE_SIZE=0`), so this is the sustained cost of answering every request rather than the cost of repeating one; `results/cache.md` is where a warm cache is measured.

- **The tail splits in two.** The writer soak reads p95 24.38 ms and the control reads 21.32 ms at the same load and the same write rate, so **+3.06 ms** of the writer soak's tail is the served tenant's ingest - its view growing and the reload that notices it - and that is the part a serving process actually pays.
- Against the short curve at this level (18.19 ms), the writer soak is +6.19 ms: +3.06 ms of it is the reload and the corpus growth the served view sees, and +3.13 ms is the cost of writing at all - WAL, a second connection, a larger file - which the control keeps and the reload does not.

- The other load model is committed beside this one: `results/soak-interactive.json` reports p95 9.89 ms at 10 users with 100 ms think time. The two answer different questions - headroom versus user traffic - and neither replaces the other.

