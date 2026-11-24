# Determinism — two runs, one answer (NFR-15, M20)

Command: `python -m eval.determinism` · the real eval command run twice, back to back,
with each run's `results/eval_report.json` and `results/baseline.json` normalised, hashed
and diffed field by field. The committed reports are restored afterwards.

| run | command | exit | seconds | content hash |
|---|---|---|---|---|
| 1 | `eval.run_eval` | 0 | 1.29 | `3634059304cc4802` |
| 2 | `eval.run_eval` | 0 | 1.25 | `3634059304cc4802` |

Keys ignored as volatile on purpose: none — the payloads carry no clock, so this is a byte-exact comparison.

Differences: **0**. Verdict: **PASS**.

Every recall, precision, MRR, nDCG, refusal rate, interval bound and per-question
miss list is identical across the two runs, which is what makes every other report
in `results/` a reproduction rather than a transcript of one lucky afternoon.
