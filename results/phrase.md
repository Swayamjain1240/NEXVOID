# Phrase-level novelty — does the corpus contain these word pairs? (M17)

Command: `python -m eval.phrase` · encoder `potion-base-8M` · theta 0.31 (unmoved) · word floor 0.50 (unmoved)
Corpus `corpus/campus (18 documents, frozen at M1)` · commit `286cc58` · 3224 distinct word pairs

The shipped gate is confidence below theta **or** the M14 word rule. One rule is added
here: refuse when at least 2 of the question's content pairs occur
nowhere in the corpus and at least the floor's share of its pairs are unseen.
Every number comes from one measurement per question, re-bucketed by floor.

## The new set, before the rule

v4 is the load-bearing set: 12 answerable and 6 unanswerable questions written after M16,
so neither signal was designed against them.

| set | answerable | unanswerable | role |
|---|---|---|---|
| v1 | 24 | 6 | fit the floor |
| v2 | 12 | 6 | replication |
| v3 | 12 | 6 | replication |
| v4 | 12 | 6 | decides |

| set | class | phrase novelty min | max | mean |
|---|---|---|---|---|
| v1 | answerable | 0.20 | 1.00 | 0.8619 |
| v1 | unanswerable | 0.67 | 1.00 | 0.8611 |
| v2 | answerable | 0.50 | 1.00 | 0.7417 |
| v2 | unanswerable | 0.75 | 1.00 | 0.9167 |
| v3 | answerable | 0.43 | 1.00 | 0.6923 |
| v3 | unanswerable | 1.00 | 1.00 | 1.0000 |
| v4 | answerable | 0.33 | 1.00 | 0.7352 |
| v4 | unanswerable | 1.00 | 1.00 | 1.0000 |

## Before and after the fitted floor

Fitted floor **1.00** — smallest grid floor strictly above the highest phrase novelty among v1 answerables;
the worst v1 answerable question has phrase novelty 1.0000.

| set | baseline answer | baseline refusal | + phrase answer | + phrase refusal |
|---|---|---|---|---|
| v1 | 0.9583 | 0.8333 | 0.5000 | 0.8333 |
| v2 | 0.9167 | 0.5000 | 0.8333 | 0.8333 |
| v3 | 1.0000 | 0.6667 | 0.9167 | 1.0000 |
| v4 | 1.0000 | 0.6667 | 0.7500 | 1.0000 |

## Every floor, every set

| floor | v1 answer | v1 refusal | v2 answer | v2 refusal | v3 answer | v3 refusal | v4 answer | v4 refusal |
|---|---|---|---|---|---|---|---|---|
| 0.33 | 0.0417 | 1.0000 | 0.0000 | 1.0000 | 0.0000 | 1.0000 | 0.0000 | 1.0000 |
| 0.40 | 0.0417 | 1.0000 | 0.0000 | 1.0000 | 0.0000 | 1.0000 | 0.0833 | 1.0000 |
| 0.50 | 0.0417 | 1.0000 | 0.0000 | 1.0000 | 0.1667 | 1.0000 | 0.0833 | 1.0000 |
| 0.60 | 0.0833 | 1.0000 | 0.0833 | 1.0000 | 0.4167 | 1.0000 | 0.1667 | 1.0000 |
| 0.67 | 0.1667 | 1.0000 | 0.5000 | 1.0000 | 0.4167 | 1.0000 | 0.4167 | 1.0000 |
| 0.75 | 0.1667 | 1.0000 | 0.5000 | 1.0000 | 0.5833 | 1.0000 | 0.5000 | 1.0000 |
| 1.00 (fitted) | 0.5000 | 0.8333 | 0.8333 | 0.8333 | 0.9167 | 1.0000 | 0.7500 | 1.0000 |

## Decision

- **Not adopted** at floor 1.00: v4 refusal 0.6667 → 1.0000, answer rate v1 0.9583 → 0.5000, v4 1.0000 → 0.7500. The rule, not the number, is the result.

## v4 in full

Every question on the decision set, with both signals and both decisions. Six
unanswerable questions means one row is worth 0.1667 of refusal accuracy.

| id | class | confidence | phrase novelty | unseen pairs | refused before | after |
|---|---|---|---|---|---|---|
| v4-001 | answerable | 0.495 | 0.714 | admission counselling, aggregate physic, eligible admission, mathematic eligible, minimum aggregate | no | no |
| v4-002 | answerable | 0.604 | 0.333 | fee adjusted, tuition later | no | no |
| v4-003 | answerable | 0.523 | 0.800 | curfew time, hostel curfew, permission late, time permission | no | no |
| v4-004 | answerable | 0.389 | 1.000 | day time, parent visit, time parent, visit hostel | no | yes |
| v4-005 | answerable | 0.590 | 0.600 | day borrowing, fine day, library fine | no | no |
| v4-006 | answerable | 0.371 | 1.000 | book borrow, borrow once, long keep, once long | no | yes |
| v4-007 | answerable | 0.503 | 0.667 | dispensary timing, timing doctor | no | no |
| v4-008 | answerable | 0.414 | 1.000 | counsellor sit, day doe, doe counsellor, sit timing | no | yes |
| v4-009 | answerable | 0.489 | 0.833 | committee impose, damaging property, fine discipline, impose damaging, maximum fine | no | no |
| v4-010 | answerable | 0.435 | 0.750 | day have, fee day, have apply | no | no |
| v4-011 | answerable | 0.504 | 0.500 | instalment due, tuition two | no | no |
| v4-012 | answerable | 0.597 | 0.625 | eligible based, income student, percentage tuition, scholarship percentage, tuition waived | no | no |
| v4-013 | unanswerable | 0.420 | 1.000 | charge doe, doe carry, extra charge, hostel room, occupancy hostel, room extra, single occupancy, switch single | no | yes |
| v4-014 | unanswerable | 0.396 | 1.000 | after graduation, certificate after, degree certificate, duplicate degree, fee duplicate | yes | yes |
| v4-015 | unanswerable | 0.374 | 1.000 | alumni association, association membership, date pay, last date, membership fee, pay alumni | no | yes |
| v4-016 | unanswerable | 0.163 | 1.000 | bus main, doe run, gate railway, main gate, often doe, railway station, shuttle bus, station often | yes | yes |
| v4-017 | unanswerable | 0.255 | 1.000 | assistant library, hour week, hourly wage, library hourly, student assistant, week work, work student | yes | yes |
| v4-018 | unanswerable | 0.225 | 1.000 | affair office, current dean, dean student, student affair | yes | yes |

## What this signal still cannot see

- **Dilution.** A share falls when a question is wordy; a single unseen pair beside many
  known ones cannot move the number. That is measured here, not argued.
- **A topic built from attested pairs.** Two real words in their usual order can name a
  thing the corpus does not have. Vocabulary, at either level, cannot refute meaning.
- **Corpus size.** Both floors are fitted on 18 documents; fewer need a re-fit.

## Reproducing

```
make phrase
```
