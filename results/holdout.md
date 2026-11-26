# Holdout — the out-of-sample test (M11)

Encoder `potion-base-8M`. v1 is the frozen 24+6 set every earlier number used; v2 is an
appended 12+6 set written after the code was frozen, with its own ids. Nothing in v1
was edited, and no v2 question was used to choose a weight, a threshold or a ranker.
v3 is the second appended set (12+6, M13), written after the v2 refusal failure was
already known and scored here for the first time. **Decisions below are read off v3.**
v4 is the set written for the M17 novelty question (12+6); it is scored here too, but
it was not written for retrieval, so its recall column is a check, not a target.
v5 is the M28 set: 30 unanswerable questions and no answerable ones, written because six
questions per set made one question worth 0.1667 of refusal accuracy. It scores in the
gate only, and it is the first set here whose rate does not move one question at a time.
v6 is its mirror (M29): 30 answerable questions and no unanswerable ones, so a false
refusal is worth 0.0333 too. The two together price both sides of the gate.

## Retrieval, all four sets, same configurations

| configuration | v1 recall@10 (95% CI) | v2 recall@10 | v3 recall@10 | v4 recall@10 | v6 recall@10 | v3 p@5 capped | v3 MRR |
|---|---|---|---|---|---|---|---|
| sparse | 0.9583 [0.798, 0.993] | 1.0000 | 1.0000 | 1.0000 | 1.0000 | 1.0000 | 1.0000 |
| dense | 1.0000 [0.862, 1.000] | 1.0000 | 1.0000 | 1.0000 | 1.0000 | 1.0000 | 0.9583 |
| hybrid (fusion only) | 0.9583 [0.798, 0.993] | 1.0000 | 1.0000 | 1.0000 | 1.0000 | 1.0000 | 0.9583 |
| hybrid + blend | 0.9167 [0.742, 0.977] | 1.0000 | 1.0000 | 1.0000 | 1.0000 | 1.0000 | 1.0000 |
| hybrid + learned | 0.9583 [0.798, 0.993] | 1.0000 | 1.0000 | 1.0000 | 1.0000 | 1.0000 | 1.0000 |

5 of five configurations reach 1.0000 on
v2, 5 of five on v3, 5 of five on v4 and 5 of five on v6, so all four appended
answerable sets
are saturated: they say only that no configuration misses a relevant document on
questions like these, which are short, factual and single-document - exactly the traffic
this corpus produces. Neither set can rank the rankers. Their teeth are in the gate, and
that is where both earn their place.

## The gate on the new set: answer rate and refusal accuracy

| set | theta | answer rate (95% CI) | refusal accuracy (95% CI) | n answerable | n unanswerable |
|---|---|---|---|---|---|
| v1 | 0.31 | 0.9583 [0.798, 0.993] | 0.8333 [0.436, 0.970] | 24 | 6 |
| v2 | 0.31 | 0.9167 [0.646, 0.985] | 0.3333 [0.097, 0.700] | 12 | 6 |
| v3 | 0.31 | 1.0000 [0.757, 1.000] | 0.5000 [0.188, 0.812] | 12 | 6 |
| v4 | 0.31 | 1.0000 [0.757, 1.000] | 0.5000 [0.188, 0.812] | 12 | 6 |
| v5 | 0.31 | - (no answerable questions) | 0.3667 [0.219, 0.545] | 0 | 30 |
| v6 | 0.31 | 1.0000 [0.886, 1.000] | - (no unanswerable questions) | 30 | 0 |

## The same gate with the vocabulary signal

`refusal_novelty_floor = 0.50` (M14, `results/support.md`) refuses when the confidence gate already would, or when at least two of the question's content terms occur nowhere in the corpus and at least half of them do not. It reads the whole tenant vocabulary rather than the one passage the gate chose, so it is the only signal here that can see a topic the corpus does not carry. Confidence and novelty are each measured once, so this table is the first one re-bucketed, not a second run.

| set | answer rate (95% CI) | refusal accuracy (95% CI) | delta vs confidence only |
|---|---|---|---|
| v1 | 0.9583 [0.798, 0.993] | 0.8333 [0.436, 0.970] | +0.0000 |
| v2 | 0.9167 [0.646, 0.985] | 0.5000 [0.188, 0.812] | +0.1667 |
| v3 | 1.0000 [0.757, 1.000] | 0.6667 [0.300, 0.903] | +0.1667 |
| v4 | 1.0000 [0.757, 1.000] | 0.6667 [0.300, 0.903] | +0.1667 |
| v5 | - (no answerable questions) | 0.5000 [0.332, 0.668] | +0.1333 |
| v6 | 1.0000 [0.886, 1.000] | - (no unanswerable questions) | - |

On v5 the floor refuses 15 of 30 unanswerable questions where confidence alone refused 11, so the combined decision reads 0.5000 against 0.3667. The 4 it adds are v5-015, v5-017, v5-020, v5-023: several content words absent and confidence still above theta. It cannot touch v5-021, v5-022, where every word is attested and novelty is 0.000 - a rule that counts absent words has nothing to count there, which is exactly the case `results/support.md` calls its blind spot. The signal is real and it is not sufficient: half of v5 is still answered.

## v5, question by question

One row is 0.0333 of the rate above, which is the point of the set: a change of one row is no longer a change of the headline. Each `note` in `eval/golden_set_v5.jsonl` records whether the question is a topic the corpus has no word for or a near-miss.

| id | confidence | novelty | unseen terms | refused (confidence) | refused (+ floor) |
|---|---|---|---|---|---|
| v5-001 | 0.421 | 0.167 | laundry | no | no |
| v5-002 | 0.163 | 0.750 | pool, swimming, weekend | yes | yes |
| v5-003 | 0.208 | 0.500 | teache, yoga | yes | yes |
| v5-004 | 0.180 | 0.800 | bring, scooter, sticker, vehicle | yes | yes |
| v5-005 | 0.280 | 0.429 | guest, house, tariff | yes | yes |
| v5-006 | 0.325 | 0.200 | photocopying | no | no |
| v5-007 | 0.291 | 0.600 | bring, convocation, guest | yes | yes |
| v5-008 | 0.247 | 0.600 | convocation, gown, rent | yes | yes |
| v5-009 | 0.419 | 0.333 | club, funding | no | no |
| v5-010 | 0.265 | 0.500 | council, election, nomination | yes | yes |
| v5-011 | 0.267 | 0.600 | anti, harassment, helpline | yes | yes |
| v5-012 | 0.407 | 0.125 | insurance | no | no |
| v5-013 | 0.310 | 0.250 | nss | no | no |
| v5-014 | 0.289 | 0.500 | concession, here, sibling, study | yes | yes |
| v5-015 | 0.329 | 0.500 | heater, install, winter | no | yes |
| v5-016 | 0.365 | 0.400 | machine, washing | no | no |
| v5-017 | 0.343 | 0.600 | bring, furniture, own | no | yes |
| v5-018 | 0.383 | 0.400 | children, daycare | no | no |
| v5-019 | 0.326 | 0.200 | locker | no | no |
| v5-020 | 0.310 | 0.500 | inside, smoking | no | yes |
| v5-021 | 0.544 | 0.000 | - | no | no |
| v5-022 | 0.585 | 0.000 | - | no | no |
| v5-023 | 0.341 | 0.500 | arrange, education, partner | no | yes |
| v5-024 | 0.381 | 0.250 | printing | no | no |
| v5-025 | 0.266 | 0.750 | join, selection, unit | yes | yes |
| v5-026 | 0.434 | 0.333 | bill, rebate | no | no |
| v5-027 | 0.491 | 0.125 | trip | no | no |
| v5-028 | 0.293 | 0.000 | - | yes | yes |
| v5-029 | 0.396 | 0.333 | duplicate | no | no |
| v5-030 | 0.323 | 0.400 | canteen, serve | no | no |

## Why it fails: the classes overlap on the only feature the gate uses

| set | class | confidence min | max | mean |
|---|---|---|---|---|
| v1 | answerable | 0.306 | 0.557 | 0.426 |
| v1 | unanswerable | 0.218 | 0.328 | 0.274 |
| v2 | answerable | 0.297 | 0.622 | 0.493 |
| v2 | unanswerable | 0.219 | 0.434 | 0.351 |
| v3 | answerable | 0.365 | 0.602 | 0.507 |
| v3 | unanswerable | 0.214 | 0.497 | 0.326 |
| v4 | answerable | 0.371 | 0.604 | 0.493 |
| v4 | unanswerable | 0.163 | 0.420 | 0.306 |
| v5 | answerable | - | - | - |
| v5 | unanswerable | 0.163 | 0.585 | 0.339 |
| v6 | answerable | 0.342 | 0.687 | 0.547 |
| v6 | unanswerable | - | - | - |

On v1 the six unanswerable questions all sit below the six weakest answerable ones,
which is why theta had a clean gap to land in. On v2 they do not: the lowest
answerable question scores 0.297 and the highest
unanswerable one scores 0.434. No theta separates
the two classes, and across the whole grid the best v2 refusal accuracy reachable is
0.5000 - at the cost of refusing questions the system can answer.

v3 repeats the pattern with different numbers, which is what makes it a replication and
not an anecdote: the weakest answerable question scores
0.365, the strongest unanswerable one
0.497, and the best refusal accuracy any theta can
reach is 0.8333. The classes overlap on confidence in all three sets;
the first set only looked separable because six questions happened to be easy to refuse.
v5 puts the same question to thirty questions instead of six: the best refusal accuracy
any theta in the grid reaches there is 0.7667, and on v4 it is
0.8333. Thirty questions do not find a threshold that six could not; they
only measure how far short it falls.

| theta | v1 answer | v1 refusal | v2 answer | v2 refusal | v3 answer | v3 refusal |
|---|---|---|---|---|---|---|
| 0.20 | 1.0000 | 0.0000 | 1.0000 | 0.0000 | 1.0000 | 0.0000 |
| 0.25 | 1.0000 | 0.3333 | 1.0000 | 0.1667 | 1.0000 | 0.1667 |
| 0.28 | 1.0000 | 0.5000 | 1.0000 | 0.1667 | 1.0000 | 0.1667 |
| 0.30 | 1.0000 | 0.8333 | 0.9167 | 0.1667 | 1.0000 | 0.3333 |
| 0.31 (shipped) | 0.9583 | 0.8333 | 0.9167 | 0.3333 | 1.0000 | 0.5000 |
| 0.33 | 0.9167 | 1.0000 | 0.9167 | 0.3333 | 1.0000 | 0.6667 |
| 0.35 | 0.8750 | 1.0000 | 0.9167 | 0.5000 | 1.0000 | 0.8333 |
| 0.38 | 0.7500 | 1.0000 | 0.9167 | 0.5000 | 0.9167 | 0.8333 |
| 0.40 | 0.5833 | 1.0000 | 0.9167 | 0.5000 | 0.9167 | 0.8333 |

The shipped theta is **not** moved. Choosing the theta with the fewest v2 mistakes
would consume the holdout and leave the next claim untested, which is the failure mode
this set exists to prevent. The requirement stays open in `README.md` with these
numbers attached.

## Fusion weights: fitted on v1, judged on v3

| sparse weight | dense weight | v1 recall@10 | v2 recall@10 | v3 recall@10 |
|---|---|---|---|---|
| 1.0 | 1.0 | 0.9167 | 1.0000 | 1.0000 |
| 1.5 | 1.0 | 0.9583 | 1.0000 | 1.0000 |
| 2.0 | 1.0 | 0.9583 | 1.0000 | 1.0000 |
| 1.0 | 0.5 | 0.9583 | 1.0000 | 1.0000 |
| 1.0 | 1.5 | 1.0000 | 1.0000 | 1.0000 |
| 1.5 | 0.5 | 0.9583 | 1.0000 | 1.0000 |

## Decisions

- **Fusion weights:** best on v1 is sparse 1.0 / dense 1.5 with v1 1.0000 and v2 1.0000, against the shipped 1.0/1.0 at v1 0.9583 and v2 1.0000. **Not adopted**: the rule was no v1 loss and a v2 gain, and this does not clear both.
- **FR-11 on the holdout:** fusion-only scores 1.0000 against dense 1.0000 and sparse 1.0000, a gap of +0.0000. The requirement is met on v2, but v2 saturates, so this is a weak pass: it shows fusion misses nothing on easy questions, not that it matches dense.
- **Refusal generalises:** no on v3 - refusal accuracy 0.8333 on v1, 0.3333 on v2, 0.5000 on v3. Two independent appended sets agree, so
the v1 figure was in-sample and the gate is the open requirement, not a rounding problem.
- **v5 narrows the estimate; it does not move the rate.** 0.3667 [0.219, 0.545] on 30 unanswerable questions (11 of 30 refused), against 0.5000 [0.188, 0.812] on the six-question v3 and v4. The point estimate is lower, but the intervals overlap, so the honest reading is a narrower estimate rather than a demonstrated decline: at these n the data are consistent with the gate refusing anywhere from about a fifth to over half of unseen topics. What v5 does rule out is the 0.8333 v1 reported, which sits above its upper bound. With the vocabulary floor it is 0.5000 (15 of 30). The requirement stays open, and the rate no longer moves one question at a time.
- **v6 measures the other error at the same resolution.** 1.0000 of 30 answerable questions are answered at the shipped theta [0.886, 1.000], so false refusals are 0.0000 [0.000, 0.114] (0 of 30) - one question is 0.0333, the resolution v5 gives the refusal side. With the vocabulary floor 30 of 30 are answered, so that signal's price is stated in the same units as its gain rather than in sixths.
- **FR-11 on v3:** fusion-only 1.0000 against dense 1.0000 and sparse 1.0000 (+0.0000). Met on v3 as well, with the same saturation caveat.

## Misses, by set and configuration

**v2**

- sparse: none
- dense: none
- hybrid (fusion only): none
- hybrid + blend: none
- hybrid + learned: none

**v3**

- sparse: none
- dense: none
- hybrid (fusion only): none
- hybrid + blend: none
- hybrid + learned: none

**v4**

- sparse: none
- dense: none
- hybrid (fusion only): none
- hybrid + blend: none
- hybrid + learned: none

**v6**

- sparse: none
- dense: none
- hybrid (fusion only): none
- hybrid + blend: none
- hybrid + learned: none

