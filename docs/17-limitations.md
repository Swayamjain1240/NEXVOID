# Limitations register

**Document ID:** ATL-LIM-001 · **Applies to:** tag `v1.9.0` · **Status:** current

Every row names a limitation, the evidence that shows it is real, and what would remove it. A row
without evidence would be a feeling, so there are none — if a gap is measurable it is in a report,
and if it is not measurable it is in the exemption list of `tests/test_traceability.py` instead.

This is not a roadmap. Two items here (FR-17, a write API) are decisions the project made and is
standing by; the rest are size and environment, which a reader should price in before quoting
anything from `results/`.

## 1. Requirements that are not met

| # | limitation | evidence | what would remove it |
|---|---|---|---|
| L1 | **FR-17 open.** The refusal gate does not generalise from v1 to the appended sets: accuracy 0.8333 (v1, n=6) → 0.3333 (v2) → 0.5000 (v3, v4) → **0.3667 [0.219, 0.545] on v5's 30 unanswerable questions**, 0.5000 with the vocabulary-novelty signal. The 30-question reading is lower than the six-question ones, so those were optimistic rather than merely noisy. | `results/holdout.md`, `results/support.md` | a signal that separates the classes on unseen topics — semantic entailment of the question against retrieved evidence, which this extractive design does not have |
| L2 | **FR-11 amended, not met literally.** Hybrid is not guaranteed to match the better of its two retrievers. At depth 10 fusion wins the one contested question; the strict form is provably reached only by copying dense. | `results/fusion.md`, ADR-0010 | a retriever whose errors are uncorrelated with dense's on a corpus this small — i.e. a bigger corpus |
| L3 | **NFR-01 is met, and the bound is exercised only under a deliberately under-provisioned configuration.** `results/overload.md` holds 4 slots with 40 ms of padding per answer and sheds **84.0%** at 50 users, showing the whole behaviour: served p95 79.05 ms, shed p95 37.02 ms, `/v1/healthz` still 200 (15.9 ms) while answers are refused. At the shipped bound of 32 slots no *committed* load sheds at all, so the default's operating point is still unexercised by the reports — the run proves the mechanism, not that 32 is the right number. A second session of the same code did reach it: 219 of 5,000 requests shed at 50 users (4.4%, p50 29.37 ms, p95 91.41 ms) in `results/bench-second-session.md`, which is the bound doing its job at the edge of this machine's variance, not a second operating point. | `results/overload.md`, `results/soak.md`, `results/bench.md` | a load that saturates 32 slots with *real* service time: a slow encoder, or a corpus large enough to make retrieval expensive |
| L4 | **Answer selection is imperfect.** The returned sentence is chosen by a scoring rule, not by comprehension: 8 of 24 v1 answers opened with a document's topic list before M15, 0 after, but a rule tuned on 24 questions is not a guarantee. | `results/answer_selection.md` | a reader model, which is stage 2 |

## 2. Things this project deliberately does not have

| # | limitation | evidence | consequence |
|---|---|---|---|
| L5 | **No generative stage.** Answers are extracted spans; there is no LLM, no streaming, no faithfulness score. | `docs/16-completion-plan.md` non-goals; NFR-02 and NFR-06 exempt in `tests/test_traceability.py` | questions the corpus answers in a different vocabulary than they are asked in are refused, not paraphrased |
| L6 | **No write API.** Ingest is `atlas.cli ingest`; there is no HTTP upload, delete or update. | ADR-0012, `results/api_contract.md` probe 10 | ingestion is an operator act with filesystem access; no multi-user editing |
| L7 | **No identity or authorization beyond one shared token.** `X-Tenant` selects a tenant; the token gates all of them. | `src/atlas/handler.py`, `results/safety.md` (tenancy probes) | one leaked token reads every tenant |
| L8 | **No rate limiting, quotas or per-client fairness.** M23 added backpressure — a bounded admission queue that sheds with `503 OVERLOADED` — but the bound is global and first-come: one client can occupy every slot, and nothing meters how many requests a tenant sends. | `src/atlas/admission.py`, `results/bench.md`, `docs/13-handover-runbook.md` | per-token quotas and a fair queue; the current bound protects the process, not the other tenants |
| L9 | **No backup, rotation or migration story.** SQLite file, replaced document by document on checksum. | `src/atlas/store.py` | losing `data/*.db` loses the index; it is rebuildable from `corpus/` only |

## 3. Limits of the evidence itself

| # | limitation | evidence | what a reader should assume |
|---|---|---|---|
| L10 | **Corpus is small and synthetic.** 18 campus policy documents, 34 chunks, one tenant's worth of content. | `corpus/campus`, `results/eval_report.md` header | the absolute numbers (recall 1.0000, p95 in tens of ms) will not transfer to a real corpus unchanged |
| L11 | **The appended sets mostly saturate.** v2, v3 and v4 all reach recall@10 1.0000 for every configuration, so they test the gate and the misses, not the ranking. v5 has no answerable questions at all, so it tests the gate and nothing else. | `results/holdout.md` | "recall 1.0000" means "no miss on 12 short factual questions", not "solved" |
| L12 | **Small n, wide intervals.** 24 answerable questions in v1; a 95% Wilson interval on 0.9583 spans 0.798–0.993. | `results/holdout.md`, `results/eval_report.md` | one question moves a rate by 4 points on v1 and 8 on an appended six-question set; v5's 30 unanswerable questions move it by 3.3, which is why the gate's headline rate is now quoted from v5 |
| L13 | **One machine, one platform.** Every number is from a Windows 11 CPU-only host, loopback HTTP, single node, stdlib server, and every load report except `results/workers.md` is one process. | every `provenance` block | cross-platform behaviour is untested; the worker curve has the same laptop offering the load it serves, and `/v1/metrics` is per process |
| L14 | **No TLS and no proxy in the measured path.** | `scripts/repro_clone.py`, `bench/sweep.py` | production TLS termination is untested and would change the latency curve |
| L15 | **Determinism is proven for the eval report only.** Two runs of `eval.run_eval` are byte-identical; latency and soak numbers are machine-dependent by nature. | `results/determinism.md` | reproducibility claims stop at the quality reports |
| L16 | **CI runs lint, tests and the gates, not the long measurements.** The 30-minute soak, the load curve and the clean-clone handover are run deliberately, not on every push. | `.github/workflows/ci.yml`, `results/soak.md` header | a regression in sustained performance would be found late |
| L17 | **The temporal evidence is a smoke test, not an archive study.** 9 dated questions over 3 policies whose 6 revisions were authored for the purpose: consistent by construction, effective dates known exactly. A real archive has undated amendments, gaps and contradictory circulars. | `results/temporal.md`, `corpus/temporal/` | a real policy archive with its own dated history, and questions written by someone who did not author the revisions |
| L19 | **The M22 speedup is a ratio on one laptop.** Two arms in one process over the same corpus, so the comparison is controlled, but the absolute numbers move with the host and the run (three runs: 2.00×, 2.12×, 2.26×). The naive arm exists only for this measurement and is selected by an environment variable nothing else sets. During M26 a second session on the same code and machine read 17.35 ms p95 at 10 users against the canonical 18.19 ms, then shed 219 of 5,000 at 50 users where the canonical run shed none (`results/bench-second-session.md`): the curve is reproducible at the knee and not above it, so a re-run does not replace a committed report. | `results/perf-m22.md`, `bench/perf.py` | repeated runs on a second machine and a second OS, reported as a range |
| L21 | **An incremental ingest is an approximation for the dense half.** `refresh()` appends new chunks and reuses every vector already computed, but hashed TF-IDF bakes corpus idf into each row, so appending a document leaves the old rows on the previous idf. On the mini corpus the ranking is unchanged (`tests/test_incremental.py`), and `results/scale.md` measures the append at 25× cheaper than a rebuild at 10k chunks — one small-corpus equivalence check, not a proof. A revised document, a closed revision window or a deletion still rebuilds. | `results/scale.md`, `src/atlas/retrieval/grow.py` | re-embedding the affected rows on a schedule, or an idf-independent encoder (stage 2) |
| L20 | **The cache's hit rate belongs to an invented mix, and the cache is per process.** `results/cache.md` draws 240 requests from 24 frozen questions and hits 90.0%; a workload with no repeats would hit 0%, and this project has no traffic log to say which is closer. With N worker processes each holds its own cache, so the pool's effective hit rate is below any single worker's and `/v1/metrics` describes one worker. | `results/cache.md`, `bench/cache.py`, L13 | a traffic log, or one shared cache behind the workers — a second store, not a configuration change |
| L22 | **Corpus-derived expansion is not adopted, and its ceiling is the corpus itself.** `results/expansion.md` fits a PMI threshold on v1 — 2.5 is the most expansion v1 tolerates — and finds it buys one v1 answer and loses one v3 refusal: v3-016 goes 0.3048 → 0.4031 once "150, advance, break" are appended, so a stray numeral and two unrelated words do the work. A term the corpus never uses has no neighbours, so the questions expansion should help most are the ones it cannot touch, and the table is corpus-wide rather than date-scoped, so under `as_of` an expanded query can carry a later revision's word. The default stays `expansion_threshold = 0.0`. | `results/expansion.md`, `src/atlas/expand.py` | a semantic expansion (embeddings of the question's terms), which is stage-2 work this project rules out; and a corpus large enough for co-occurrence to mean something |
| L18 | **Temporal support is one-directional.** A revision can be superseded, but a correction inside a revision in force cannot be dated without publishing a new revision. There is no transaction-time axis either, so the system answers "what was in force then", never "what did we say then". | ADR-0013 | a bitemporal store (valid time + recorded time), which is a new data model rather than a column |

## How to read a number from this repository

1. Find it in `README.md`; the row names its report.
2. Open the report header: platform, encoder, corpus size, commit, regenerate command.
3. Check the sample size (n) before the rate. L12 is the reason.
4. If the claim is about refusal on unseen topics, it is open (L1). Nothing else in `results/`
   should be read as a claim about questions the corpus does not answer.
