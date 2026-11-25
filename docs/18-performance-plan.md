# 18 — Performance plan (M22–M27)

**Document ID:** ATL-PERF-002 · **Applies to:** post-`v1.5.0` · **Status:** M22 complete at
`v1.6.0`, M23 complete at `v1.7.0`, M24 complete at `v1.8.0`, M25 complete at `v1.9.0`,
M26 complete at `v1.10.0`; M27 proposed

The project is complete and every claim is measured. This plan is about making the same system
faster and more predictable **without weakening the evidence discipline**: each phase ends in a
report under `results/`, on the same machine, against the same frozen sets, and the determinism
probe must still show 0 differences at the end of every phase.

Two numbers frame the work, both measured on the shipped code:

- a warm query costs **0.834 ms** on one core (≈1,200 qps), and the tenant view builds in **44 ms**;
- **71%** of that query CPU is `coverage()` re-tokenising a passage whose token set the index
  already holds, and memoising it is worth **1.95×** (0.834 → 0.427 ms) with no behaviour change.

So this is not a rewrite. It is a sequence of removals, one admission-control change, and one
quality experiment — ordered so that each phase's claim is provable before the next begins.

## Ground rules for every phase

1. **Measure first, then change.** No phase starts without its "before" number in `results/`.
2. **Same ruler.** The `bench/sweep.py` workload and the frozen golden sets are fixed; a phase that
   needs a different workload adds a curve, it does not replace one.
3. **No behaviour drift.** `make eval`, `make holdout`, `make temporal`, `make determinism` and the
   eval gate must produce the same numbers as `v1.5.0`, except where the phase is *about* a
   behaviour change (only M27).
4. **One phase, one report.** `results/perf-<phase>.md` with provenance, before/after, and the
   command that regenerates it.
5. **Ceilings hold.** Files ≤ 200 lines; runtime dependencies stay at one unless a phase justifies a
   second in writing (nothing here does).

---

## M22 — Instrument, then take the free 2×

**Why first:** the trace report has one `retrieve_ms` for five stages, so an optimisation can be
"shipped" without knowing what it moved. And the largest win needs no design decision at all.

**Changes**

- Split `retrieve_ms` into `bm25_ms`, `dense_ms`, `fuse_ms`, `rank_ms`, `embed_ms` in the answer
  payload (additive keys; the contract probe is updated to require them).
- `bench/perf.py`: N fixed queries × M iterations, warm, reporting per-query mean/p95 and qps, plus
  the cold-view build time. No network, no server — the pure pipeline.
- Memoise chunk tokenisation for `coverage()` on the tenant view (the view already holds the sets),
  embed the query once per request, and stop folding each token twice in `tokenize()`.
- Precompute the idf-weighted term totals per chunk so `coverage` becomes a sum over query terms
  rather than a re-tokenised scan.

**Expected:** 1.95× measured for the memo alone; 2.2–2.6× with the rest. **Exit criteria:** the
report shows ≥2× on mean and p95, every frozen-set number identical to `v1.5.0`, determinism 0.

**Measured at `v1.6.0` — done.** `results/perf-m22.md`: naive 0.755 ms → fast 0.342 ms mean
(**2.21×**, p95 1.469 → 0.797 ms), `fold_plural` calls per query 564 → 138, with **identical**
answers on all eight benchmark questions (the script exits non-zero if they ever differ).
Recall@10 0.9167, refusal 1.0000, temporal 9/9 with 0 leaks, determinism 0 differences — all
unchanged. Two new responses keys (`timings.retrieval`, and the contract probe that requires it)
and one new abstraction (`Confidence`) came out of it; the ceiling test still passes at 35 files.

**Risk:** a cache keyed on text can grow unbounded. Bound it to the view's chunk count (no global
memo), so it dies with the tenant — not an LRU with a policy to tune.

## M23 — Admission control: turn a failed NFR into a met one

**Why:** NFR-01 fails at saturation (p95 58.40 ms vs 50 ms) because sustained load builds a queue.
Response time is queue wait plus service time; as utilisation approaches 1, wait dominates. No
micro-optimisation fixes that — a bound does.

**Changes**

- A bounded work queue with a fixed worker count and a request deadline; when the queue is full or
  the deadline is missed, answer `503 OVERLOADED` (a new documented error code) immediately.
- Expose `queue_depth`, `rejected`, `inflight` and `shed` in `/v1/metrics`.
- Re-run the 30-minute soak at 10 unpaced users, and add a deliberate overload run where the
  system is *expected* to shed (delivered at `v1.8.0` with the bound lowered to 4 slots — see the
  measured section below for why 50 users at the shipped bound was not enough).

**Expected:** p95 ≤ 50 ms at 10 users, with a non-zero reject rate reported rather than hidden.
**Exit criteria:** `results/soak.md` regenerated; the report states the operating point, the reject
rate, and the throughput delivered.

**What actually happened at `v1.7.0`:** the bound is in (`admission.py`, `503 OVERLOADED`, capacity
in `/v1/metrics`), but the soak stopped failing for a reason the plan did not predict. M22 cut the
service time by 2.21×, which moved the 10-user operating point from p95 58.40 ms to **22.88 ms**
*before* the bound ever bound: with ~2 ms requests, ten clients keep about one request in flight,
far below `max_inflight=32`. The admission bound is a ceiling on the failure mode, not the source
of the improvement, and the reports say which is which.

The 50-user level this plan named does not reach the bound either. `results/bench.md` offers 5,000
requests there and sheds **0**: 50 clients at ~2 ms of service still occupy only a handful of the
32 slots. Shedding appears only when the bound is set below the offered load, which is what
`results/overload.md` (`v1.8.0`) does — 4 slots, 8 waiters, 40 ms of padding per answer, 50 client
processes — shedding **83.95%** (1,679 of 2,000) with served p95 79.05 ms and `/v1/healthz` still
answering 200 (15.9 ms) while every answer was refused. That demonstrates the mechanism at a
*configured* bound; it does not exercise the shipped 32, which is L3's wording and the reason the
plan's "50 users will shed" assumption is corrected here rather than left standing.

**What would make us abandon it:** if shedding more than ~2% of requests is needed to hold the
target, the honest answer is that the target belongs at a lower concurrency, and NFR-01 gets
re-scoped instead of gamed.

## M24 — Use the cores

**Why:** the process is GIL-bound at ~1,200 qps single-threaded; threads cannot help because the
work is bytecode. The constraint (no containers) does not forbid processes.

**Changes**

- `atlas.cli serve --workers N`: N child processes, each binding the same loopback port with
  `SO_REUSEPORT`, parent supervises and restarts a dead child; stdlib only.
- Per-process metrics, and a documented note that `/v1/metrics` is per worker (L13 already says the
  process is the unit; this makes it explicit rather than surprising).

**Expected:** near-linear throughput to core count at fixed latency. **Exit criteria:** a curve at
1/2/4 workers, and a statement of where it stops scaling.

**Measured at `v1.8.0` — done, with one plan correction.** `SO_REUSEPORT` does not exist on Windows,
so the shipped form is the older one: the parent creates the listening socket and each child
inherits it (`multiprocessing` duplicates the handle with `WSADuplicateSocket`). `results/workers.md`:
p95 24.96 → 11.08 → **5.10 ms** at 1/2/4 workers (**4.9×**), throughput 378 → 437 → 587 rps (1.55×).
The throughput number is capped by the *client*: at a fixed 10 users offering one request at a time,
total rps cannot exceed 10 / service time, so what the extra processes buy at this load is latency,
not work. Near-linear throughput remains unmeasured and needs a client that can offer more than the
server can take — a second machine, which L13 says this project does not have. **Risk:** the breaker and the metrics
window become per process — state that in the report, do not average across processes and call it
one system.

## M25 — Caching, with this project's two traps handled first

**Why:** identical and near-identical questions repeat; caching is the cheapest latency win
available and the most dangerous one here.

**Changes**

- **Exact cache**, keyed `(tenant, query, mode, as_of, corpus_hash)`. The `as_of` in the key is
  mandatory (M21): without it a 2024 question gets a 2026 answer. Invalidate on ingest by
  `corpus_hash`.
- **Semantic cache** (optional, second half of the phase): cosine ≥ threshold over recent query
  embeddings. The tenant is part of the lookup key *before* similarity, never after — a
  cross-tenant hit would be a data leak, not a speedup.
- Cache hit/miss counters in `/v1/metrics`, and a tenancy probe asserting a cached answer is never
  served to another tenant.

**Expected:** hit rate on a realistic repeat mix, and p95 for cached queries ≈ serialisation cost.
**Exit criteria:** `results/cache.md` with hit rate, latency by cache state, and the leak probe.
**Abandon condition:** if the measured hit rate on the frozen sets is near zero, say so and keep the
exact cache only — an unused cache is complexity with no evidence behind it.

**Measured at `v1.9.0` — done, exact cache only.** `results/cache.md`: 216 of 240 requests on a
Zipf-weighted repeat mix are hits (**90.0%**); a hit costs **1.64 ms** p50 against **2.39 ms**
with the cache off (**1.5×**); a cold miss costs 2.57 ms, inside run-to-run noise of the control,
so the cache is not a tax on traffic it cannot help. The probes are the point rather than the
speedup: two tenants asking the identical question string were answered **75 percent** and **40
percent** from their own documents, each repeat hitting its own entry; the same question at three
dates returned **three distinct revisions**, each hit returning its own date's text; and a
re-ingested document turned a warm hit into a miss carrying the new figure.

Two corrections to this section. The cache is **per process**: N workers hold N caches and N hit
rates, and `/v1/metrics` reports the one that answered. And the load reports now run with
`ATLAS_CACHE_SIZE=0`, because a 100-request-per-user curve over 24 frozen questions would
otherwise measure the cache instead of the server — so `results/bench.md` and `results/soak.md`
still describe the cost of answering every request. The **semantic half was not attempted**: the
exact cache already removes the repeated work, and a similarity cache would add a threshold to
tune plus a second way to leak. Paraphrased repeats in a real workload would be the evidence it
needs, and there is none here.

## M26 — Grow without rebuilding

**Why:** `invalidate()` drops the whole tenant view and the next query rebuilds BM25, the vector
matrix and the ranker features. At 34 chunks that is 44 ms; at 10k chunks it is seconds, on the
request path.

**Changes**

- Incremental view updates on ingest (append postings, extend the matrix, refresh features for the
  changed document only) instead of a full rebuild.
- SQLite in WAL mode so ingest can run while serving.
- A synthetic 10k-chunk corpus generator, and the cold-start/query curve that shows where exact
  search stops being sensible. Only then does ANN earn a phase — and the arithmetic in that report
  is what decides it, not fashion.

**Exit criteria:** cold start and query latency at 34 / 1k / 10k chunks, with the rebuild cost shown
separately from the query cost. **Explicit non-goal:** no ANN, no quantisation, no vector database
unless the 10k-chunk curve says the exact scan is the bottleneck.

**Measured at `v1.10.0` — done, append-only.** `results/scale.md` at 34 / 1,053 / 10,053 chunks:
cold start 36.5 → 1,439 → **10,907 ms**, warm query p50 0.06 → 2.82 → **27.49 ms**, and an ingest
into a warm view **453 ms at 10k chunks against an 11,228 ms rebuild (24.8×)**. The append is O(new
chunks) plus BM25's idf sweep over the vocabulary; the rebuild it replaces is O(corpus). WAL is on,
so the ingest runs while the process serves, and `ATLAS_RELOAD_MS` (default 1000) is how often a
serving process notices — one cheap signature query per interval per tenant, not per request.

Two limits, both in the report and L21. The append reuses every vector already computed, which is
also its limit: hashed TF-IDF bakes corpus idf into each row, so the old rows keep the idf they
were built with. `tests/test_incremental.py` shows the ranking unchanged on the mini corpus, and
that is one equivalence check, not a proof. And a revised document, a closed revision window or a
deletion still rebuilds, because none of those can be patched into a posting list — the report
measures the case M26 actually changes. **On ANN:** query p50 runs 0.06 → 2.82 → 27.49 ms across
296× the chunks — the 34-chunk reading is at timer resolution, and between the two larger levels
the growth is close to linear, as an exact scan must be. At 27 ms at 10k chunks it is not the
bottleneck this project's NFRs name, so the non-goal stands and no vector index was added.

## M27 — The quality experiment: query expansion for FR-17

**Why:** it is the only remaining correctness gap, and it is a vocabulary problem, not a latency
one. The evidence says expansion helps *and* that it can hurt: +26.7% on BM25 in one study, −7.7% to
−10.3% nDCG@10 when queries are already lexically aligned with the corpus in another.

**Changes**

- Corpus-derived expansion (co-occurrence / PMI over the tenant's chunks), no model, no key:
  expand a query term only with terms that co-occur with it in the corpus above a fitted threshold.
- Fit the threshold on v1, decide on v3, judge on v4 — the same discipline as M14 and M17, with the
  result allowed to be "no".

**Expected:** a measurable move in refusal accuracy on the appended sets, or a documented "not
adopted" with the numbers. **Exit criteria:** `results/expansion.md`, and FR-17 either improves with
no answerable question lost, or the experiment is recorded as rejected.

---

## Ordering rationale

| phase | what it buys | why it cannot move later |
|---|---|---|
| M22 | 2× on every request, and the instrumentation to see the next one | everything after it is measured with its spans |
| M23 | the failed NFR-01 becomes a passed one | needs a stable service time to set the bound |
| M24 | throughput across cores | multiplying a slow handler multiplies the wrong thing |
| M25 | removes work entirely | a cache hides a latency bug you have not fixed yet |
| M26 | scale without a rebuild cliff | only matters once the corpus grows |
| M27 | closes the last correctness gap | independent of all of the above, so it can go last without blocking |

## What this plan deliberately does not include

- **ANN / HNSW / quantisation / a vector database** — at 139 KB of vectors the exact scan costs a
  fraction of a millisecond; the plan earns these only from M26's curve.
- **Contextual or late chunking** — measured elsewhere at +3.6% relative, and a 2026 multi-objective
  study finds complex chunking rarely wins significantly over simple structure-aware chunking.
- **A new framework, `orjson`, `uvloop`, FastAPI** — each breaks the dependency budget for a
  single-digit percentage.
- **A generative stage** — that is stage 2, a different project, and it would make every latency
  number in `results/` incomparable.

## Definition of done for the plan

1. Every phase's report exists and is regenerated by a committed command.
2. `make determinism` shows 0 differences at each tag, and the frozen-set numbers are unchanged
   except in M27 where a change is the point.
3. `docs/17-limitations.md` is updated in the same commit as any number that moves — including when
   a phase *fails* to deliver.
4. No phase changes behaviour and performance at once.
