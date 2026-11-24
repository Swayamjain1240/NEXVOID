# Temporal correctness: does the answer respect the date asked about? (M21)

Command: `python -m eval.temporal` · commit `6db33cc` · corpus `corpus/campus` + `corpus/temporal` (18 documents, 6 dated revisions)
Platform: Windows-11-10.0.26300-SP0 · Python 3.12.10 · encoder `hashed-tfidf-d1024`

## Summary

| measure | value | n |
|---|---|---|
| version accuracy (top passage is the revision in force) | 1.0000 [0.701, 1.000] | 9 |
| not-yet accuracy (refused *and* the date named) | 1.0000 [0.439, 1.000] | 3 |
| **future-leak rate** (citation dated after the question) | **0.0000** | 12 |
| answer rate on in-force questions | 1.0000 | 9 |
| control: undated runs citing a not-yet-effective revision | 4 of 9 | 9 |
| control: undated runs answering a not-yet-existing rule | 3 of 3 | 3 |

## Per question

| id | as_of | asked | top revision | verdict | refused | reason | control (undated) |
|---|---|---|---|---|---|---|---|
| t01 | 2024-06-01 | What is the minimum attendance required in e... | base (expect base) | ok | no | - | 2025-01-01 **leak** |
| t02 | 2025-06-01 | What is the minimum attendance required in e... | 2025-01-01 (expect 2025-01-01) | ok | no | - | 2025-01-01 |
| t03 | 2026-09-01 | What is the minimum attendance required in e... | 2026-07-01 (expect 2026-07-01) | ok | no | - | 2025-01-01 |
| t04 | 2024-06-01 | What is the late fee for a payment made afte... | base (expect base) | ok | no | - | 2025-01-01 **leak** |
| t05 | 2025-06-01 | What is the late fee for a payment made afte... | 2025-01-01 (expect 2025-01-01) | ok | no | - | 2025-01-01 |
| t06 | 2026-09-01 | What is the late fee for a payment made afte... | 2026-07-01 (expect 2026-07-01) | ok | no | - | 2025-01-01 |
| t07 | 2024-06-01 | What is the minimum duration of a recognised... | base (expect base) | ok | no | - | 2026-07-01 **leak** |
| t08 | 2025-06-01 | What is the minimum duration of a recognised... | 2025-01-01 (expect 2025-01-01) | ok | no | - | 2026-07-01 **leak** |
| t09 | 2026-09-01 | What is the minimum duration of a recognised... | 2026-07-01 (expect 2026-07-01) | ok | no | - | 2026-07-01 |
| t10 | 2024-06-01 | What attendance do laboratory and workshop c... | base (expect not_yet_in_effect) | ok | yes | not_yet_in_effect | 2026-07-01 **leak** |
| t11 | 2024-06-01 | What refund applies to a student who transfe... | base (expect not_yet_in_effect) | ok | yes | not_yet_in_effect | 2026-07-01 **leak** |
| t12 | 2024-06-01 | What approval is needed to do a second inter... | base (expect not_yet_in_effect) | ok | yes | not_yet_in_effect | 2026-07-01 **leak** |

## What the date changes

- With `as_of`, **0.0000** of the 12 questions return evidence dated after the moment asked about. The filter runs inside both retrievers, before fusion, so a superseded revision cannot even vote.
- Without it, **4 of 9** dated questions are answered from a revision that did not exist yet, and all **3 of 3** not-yet questions are answered instead of refused. That is the failure mode the feature removes, measured rather than asserted.
- A refusal now distinguishes three cases: not close enough (`low_confidence`), a topic the corpus never carries (`unknown_terms`), and a rule that exists but not yet (`not_yet_in_effect`, naming the date). 3/3 pre-existence questions get the third, with the right date.

## How this evidence can mislead

- The revisions are **authored**, not collected: they are consistent by construction, where a real archive has gaps, contradictory circulars and undated amendments.
- Effective dates are known exactly here. In a real corpus they are frequently unknown, which is why an undated document is treated as in force at every date rather than guessed at.
- Nine dated questions across three policies is a smoke test of the mechanism, not a measure of policy-archive quality: the 95% interval on 1.0000 spans 0.701 to 1.000, so it bounds the failure rate it rules out rather than proving the rate. See `docs/17-limitations.md`.

## Reproducing

```
make temporal
```

The harness builds a fresh database in a temporary directory, ingests the base corpus and then the six dated revisions, and writes nothing into `data/`.
