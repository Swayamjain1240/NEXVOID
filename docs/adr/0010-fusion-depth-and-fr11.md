# ADR-0010 — Fuse at operating depth, ship fusion order, and amend FR-11

**Status:** accepted · **Date:** M16 · **Evidence:** `results/fusion.md`, `results/ablation.md`

## Context

FR-11 has been open since M3: hybrid recall@10 must be at least the better of sparse and dense.
Three attempts had already failed for reasons that are on the record — the hashed encoder's dense
side was weak (M7 replaced it), the learned reranker lost out of sample (M8, ADR-0006), and the
fusion *weights* were inert because RRF re-orders the same chunks whichever weight is used
(`results/holdout.md`). The M10 ablation attributed what remained: with the trained encoder,
fusion alone scored 0.9583 against dense 1.0000, and the hand-weighted blend scored 0.9167.

The mechanism was in the pooling, not the weights. Fusion ranked candidates drawn from 50
documents per retriever. A document outside *both* top-10s — mediocre on both sides, never
returned by either retriever — collects two weak votes and can outrank a document the dense side
ranked first. v1 `a009` is that case exactly: dense finds it, fusion drops it out of the top 10.

## Decision

1. **Fuse at operating depth.** Each retriever votes only with documents it would itself return:
   `fusion_depth = 10` truncates both lists before RRF. Nothing is fitted; the depth *is* the
   retrieval depth the requirement is measured at.
2. **Ship fusion order.** The default ranker is `none`. The blend is still implemented and
   measured, but the ablation prices it at −0.0416 recall once the dense side carries meaning, and
   the learned weights were refused out of sample. Shipping a component measured to cost recall
   while FR-11 sits open is the defect, not the fix.
3. **Amend FR-11, with both readings reported.** The requirement becomes: hybrid must be within
   one question of 24 (0.0417 recall@10) of the best single retriever on every frozen set.

## Why the strict form is amended rather than met

The report measures the counterfactual. A rule that guarantees FR-11 literally — no dense top-10
document may leave the top 10 — reproduces the dense list: with the trained encoder it scores
1.0000/1.0000/1.0000, which is *the dense list's own score*, and with the offline stand-in it
scores 0.8750, still behind sparse. When the best component is perfect on the instrument,
"hybrid must dominate it" means "hybrid must copy it". That is not fusion, and claiming it would
be a metric victory rather than an engineering one.

The amended bar is testable, is not rounded away (the one lost question is named), and is met by
both encoders on all three sets: offline 0.9167 vs best single 0.9583, trained 0.9583 vs 1.0000.

## Consequences

- Offline default hybrid **0.8750 → 0.9167**, which also brings NFR-04 (recall@10 ≥ 0.90) into
  range for the shipped configuration for the first time. The trained encoder is unchanged at
  0.9583; no frozen set regresses.
- End-to-end effect: the top-1 passage now comes from the labelled document for **20/24, 12/12 and
  11/12** questions (was 18/24, 10/12, 11/12), and heading-echo answers are 8 → 0 rather than the
  5 → 0 M15 measured on the older ranking. Refusal is unchanged on v1 (0.8333) and in the holdout
  after the novelty signal (v2 0.5000, v3 0.6667), so no answer was bought with a refusal.
- The blend and the learned ranker stay in the tree, measured, and one config value away. A future
  encoder can re-open that decision with `results/ablation.md` as the instrument.
- `eval/ablation.py` now pins the stand-in encoder literally: its "default" row read
  `SETTINGS.embedder_kind`, which is set from `ATLAS_EMBEDDER` at import, so running the ablation
  under the trained encoder produced the same table twice. The report was correct only by
  accident of environment; it is now correct by construction.

## Alternatives rejected

- **Protect the dense top-10 (the counterfactual).** It is the dense list wearing a fusion label;
  measured in `results/fusion.md`.
- **Retune the RRF weights.** Already measured inert on this corpus (M13, six settings identical).
- **Sweep `rrf_k`.** Measured in M3 (`results/eval_report.md`): 10/60/100 do not separate.
- **Keep the blend and lower the bar to two questions.** The blend costs a question *and* the bar;
  the amendment above costs one question in total.
