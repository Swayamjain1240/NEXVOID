# Limitations register

**Document ID:** ATL-LIM-001 · **Applies to:** tag `v1.4` · **Status:** current

Every row names a limitation, the evidence that shows it is real, and what would remove it. A row
without evidence would be a feeling, so there are none — if a gap is measurable it is in a report,
and if it is not measurable it is in the exemption list of `tests/test_traceability.py` instead.

This is not a roadmap. Two items here (FR-17, a write API) are decisions the project made and is
standing by; the rest are size and environment, which a reader should price in before quoting
anything from `results/`.

## 1. Requirements that are not met

| # | limitation | evidence | what would remove it |
|---|---|---|---|
| L1 | **FR-17 open.** The refusal gate does not generalise from v1 to the appended sets: accuracy 1.0000 → 0.1667 (v2) → 0.5000 (v3), 0.6667 with the vocabulary-novelty signal. | `results/holdout.md`, `results/support.md` | a signal that separates the classes on unseen topics — semantic entailment of the question against retrieved evidence, which this extractive design does not have |
| L2 | **FR-11 amended, not met literally.** Hybrid is not guaranteed to match the better of its two retrievers. At depth 10 fusion wins the one contested question; the strict form is provably reached only by copying dense. | `results/fusion.md`, ADR-0010 | a retriever whose errors are uncorrelated with dense's on a corpus this small — i.e. a bigger corpus |
| L3 | **NFR-01 fails under saturation.** 30 minutes at 10 unpaced users holds p95 58.40 ms against a 50 ms target; the burst test (37.2 ms) and the human-paced soak (19.56 ms) both pass. | `results/soak.md`, `results/bench.md`, `results/soak-interactive.md` | a queue with a hard admission bound — i.e. a real server, not the stdlib threading server |
| L4 | **Answer selection is imperfect.** The returned sentence is chosen by a scoring rule, not by comprehension: 8 of 24 v1 answers opened with a document's topic list before M15, 0 after, but a rule tuned on 24 questions is not a guarantee. | `results/answer_selection.md` | a reader model, which is stage 2 |

## 2. Things this project deliberately does not have

| # | limitation | evidence | consequence |
|---|---|---|---|
| L5 | **No generative stage.** Answers are extracted spans; there is no LLM, no streaming, no faithfulness score. | `docs/16-completion-plan.md` non-goals; NFR-02 and NFR-06 exempt in `tests/test_traceability.py` | questions the corpus answers in a different vocabulary than they are asked in are refused, not paraphrased |
| L6 | **No write API.** Ingest is `atlas.cli ingest`; there is no HTTP upload, delete or update. | ADR-0012, `results/api_contract.md` probe 10 | ingestion is an operator act with filesystem access; no multi-user editing |
| L7 | **No identity or authorization beyond one shared token.** `X-Tenant` selects a tenant; the token gates all of them. | `src/atlas/handler.py`, `results/safety.md` (tenancy probes) | one leaked token reads every tenant |
| L8 | **No rate limiting, quotas or backpressure.** | `docs/13-handover-runbook.md` | a single client can saturate the process; L3 is what that looks like |
| L9 | **No backup, rotation or migration story.** SQLite file, replaced document by document on checksum. | `src/atlas/store.py` | losing `data/*.db` loses the index; it is rebuildable from `corpus/` only |

## 3. Limits of the evidence itself

| # | limitation | evidence | what a reader should assume |
|---|---|---|---|
| L10 | **Corpus is small and synthetic.** 18 campus policy documents, 34 chunks, one tenant's worth of content. | `corpus/campus`, `results/eval_report.md` header | the absolute numbers (recall 1.0000, p95 in tens of ms) will not transfer to a real corpus unchanged |
| L11 | **The appended sets mostly saturate.** v2, v3 and v4 all reach recall@10 1.0000 for every configuration, so they test the gate and the misses, not the ranking. | `results/holdout.md` | "recall 1.0000" means "no miss on 12 short factual questions", not "solved" |
| L12 | **Small n, wide intervals.** 24 answerable questions in v1; a 95% Wilson interval on 0.9583 spans 0.798–0.993. | `results/holdout.md`, `results/eval_report.md` | one question moves a rate by 4 points on v1 and 8 on an appended set |
| L13 | **One machine, one platform, one process.** Every number is from a Windows 11 CPU-only host, loopback HTTP, single node, stdlib server. | every `provenance` block | cross-platform and multi-process behaviour is untested; `/v1/metrics` is per process |
| L14 | **No TLS and no proxy in the measured path.** | `scripts/repro_clone.py`, `bench/sweep.py` | production TLS termination is untested and would change the latency curve |
| L15 | **Determinism is proven for the eval report only.** Two runs of `eval.run_eval` are byte-identical; latency and soak numbers are machine-dependent by nature. | `results/determinism.md` | reproducibility claims stop at the quality reports |
| L16 | **CI runs lint, tests and the gates, not the long measurements.** The 30-minute soak, the load curve and the clean-clone handover are run deliberately, not on every push. | `.github/workflows/ci.yml`, `results/soak.md` header | a regression in sustained performance would be found late |
| L17 | **The temporal evidence is a smoke test, not an archive study.** 9 dated questions over 3 policies whose 6 revisions were authored for the purpose: consistent by construction, effective dates known exactly. A real archive has undated amendments, gaps and contradictory circulars. | `results/temporal.md`, `corpus/temporal/` | a real policy archive with its own dated history, and questions written by someone who did not author the revisions |
| L18 | **Temporal support is one-directional.** A revision can be superseded, but a correction inside a revision in force cannot be dated without publishing a new revision. There is no transaction-time axis either, so the system answers "what was in force then", never "what did we say then". | ADR-0013 | a bitemporal store (valid time + recorded time), which is a new data model rather than a column |

## How to read a number from this repository

1. Find it in `README.md`; the row names its report.
2. Open the report header: platform, encoder, corpus size, commit, regenerate command.
3. Check the sample size (n) before the rate. L12 is the reason.
4. If the claim is about refusal on unseen topics, it is open (L1). Nothing else in `results/`
   should be read as a claim about questions the corpus does not answer.
