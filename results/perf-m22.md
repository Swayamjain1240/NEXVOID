# M22 — the retrieval path, before and after (NFR-01, NFR-14)

Command: `python -m bench.perf` · commit `c1fb380` · corpus `corpus/campus (18 documents, 34 chunks)`
Platform: Windows-11-10.0.26300-SP0 · Python 3.12.10 · encoder `hashed-tfidf-d1024` · 120 iterations × 8 questions × 3 alternating repeats (table = median run)

Both arms run in one process over one corpus, so the ratio is not a cross-machine
comparison. `naive` is the `v1.5.0` path (`ATLAS_PERF_NAIVE=1`); `fast` is shipped.

## Result

| arm | mean | p50 | p95 | qps | `fold_plural` calls/query | cold view build |
|---|---|---|---|---|---|---|
| naive (v1.5.0) | 0.755 ms | 0.673 ms | 1.469 ms | 1324 | 564 | 31.2 ms |
| fast (M22) | 0.342 ms | 0.216 ms | 0.797 ms | 2924 | 138 | 31.2 ms |

**Speedup: 2.21×** on the mean, 1.84× on p95.

Per-run means (naive): [0.7535, 0.7552, 1.2097] · (fast): [0.342, 0.3337, 0.5334] ms. The arms alternate, so a slow moment hits both.

**Identical outputs: yes.** The two arms produce the same text, citation, confidence and refusal decision for all 8 questions; the script exits non-zero if they ever diverge, so a speedup can never come from doing less work.

## Where the time goes now

Mean milliseconds per stage for the fast arm, from its own `timings.retrieval`:

| stage | ms |
|---|---|
| `embed_ms` | 0.0723 |
| `bm25_ms` | 0.0305 |
| `dense_ms` | 0.0162 |
| `fuse_ms` | 0.0120 |
| `rank_ms` | 0.0019 |
| `cosine_ms` | 0.0034 |
| `coverage_ms` | 0.0199 |

## What changed

1. **Coverage reads the cached token sets.** The tenant view has held every chunk's
   tokens and bigrams since M5, for the reranker; the confidence gate nevertheless
   re-tokenised the winning passage on every request. It now uses `coverage_of_tokens`
   over the cached sets, which is the same arithmetic on the same inputs.
2. **One query embedding.** `_dense_search` embedded the query and `diagnose` embedded
   it again to compute cosine. The vector is now computed once in `diagnostics` and
   passed to both.
3. **`tokenize` folds each token once.** The comprehension called `fold_plural` twice
   per word - once to build the token, once to test the stopword list.
4. **The five retrieval stages are timings, not a story.** `retrieve_ms` used to cover
   BM25, vectors, fusion, ranking, cosine and coverage; the breakdown is in every
   response, nested under `timings.retrieval` so `serialize_ms` cannot double-count it.

## What did not change

- Every frozen-set number: `make eval` (recall@10 0.9167, refusal 1.0000),
  `make temporal` (9/9, 0 leaks) and the determinism probe (0 differences) are
  identical to `v1.5.0`. The arms assert it directly for the eight benchmark questions.
- The gate's threshold, the ranking order and the refusal logic: this phase removes
  repeated work, it does not change a decision.

## How to read this honestly

- These are 120 iterations of 8 questions on one Windows laptop over no network: the *ratio* is robust, the absolute
  numbers are machine-specific and a different host will move both columns.
- 3 alternating repeats are reported as a median; separate invocations of this script measured 2.0×, 2.1×, 2.3× and 2.9×, so quote the ratio with its range. The 2.9× run happened while other benchmarks shared the machine.
- The speedup is bounded by how much of a request was re-tokenisation. A longer answer
  (stage 2, with generation) would dilute it, and a bigger corpus would not change it,
  because the cost removed was per-request, not per-chunk.
- The naive arm is maintained deliberately and is the only thing that selects it;
  production is always the fast path.
