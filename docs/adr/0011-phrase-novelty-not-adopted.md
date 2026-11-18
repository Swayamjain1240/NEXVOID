# ADR-0011 — Phrase-level novelty was measured on a fresh set and refused

**Status:** rejected (not adopted) · **Date:** M17 · **Evidence:** `results/phrase.md`

## Context

ADR-0008 closed with a specific, testable claim: the word-level novelty signal cannot refute a
question whose words the corpus all uses. v3's sports-scholarship question — "sports",
"scholarship", "attendance", all attested, no sports scholarship in the corpus — was answered from
a different scholarship document. The named next step was meaning, not more vocabulary.

There is one rung between the two. A word can be attested while the *claim* it names is not: the
corpus says "merit scholarship" and "need-based scholarship", never "sports scholarship". So this
phase tested the obvious extension — count the question's adjacent content-word pairs that occur
nowhere in the corpus — on a set written for it.

## Method, fixed before the numbers

- **A fresh set decides.** v1–v3 have all been read by earlier phases; a new signal judged on them
  would be judged on questions it was invented after. `eval/golden_set_v4.jsonl` appends 12
  answerable and 6 unanswerable questions, the last three near-misses whose words the corpus does
  use (student library job, dean of student affairs, single-occupancy room).
- **Fit on v1.** The floor is the smallest grid value strictly above the highest phrase novelty
  among v1's answerable questions. Theta and the M14 word floor do not move.
- **Decide on v4.** Adopt only if v4 refusal improves and no set's answer rate falls.
- The signal is corpus-level and passage-blind, like its predecessor: no cosine, no chosen passage.

## Result

It refuses everything, including questions the corpus answers:

| floor | v1 answer | v1 refusal | v4 answer | v4 refusal |
|---|---|---|---|---|
| 0.33 | 0.0417 | 1.0000 | 0.0000 | 1.0000 |
| 0.67 | 0.1667 | 1.0000 | 0.4167 | 1.0000 |
| 1.00 (fitted) | 0.5000 | 0.8333 | 0.7500 | 1.0000 |

The fitted floor is 1.00 because **a002, an answerable condonation question, has phrase novelty
1.0000**: every pair in it ("after relaxation", "attendance fell", "below sixty") is phrased
differently from the policy that answers it. That is the whole finding. Unanswerable questions do
score 1.0, but so do v4-004 ("visit hostel", "parent visit"), v4-006 ("book borrow") and v4-008
("counsellor sit"), which the corpus answers with a single sentence. Question phrasing is not
corpus phrasing, at either level.

At the fitted floor, v4 refusal goes 0.6667 → 1.0000 and v4 answer rate 1.0000 → 0.7500; v1 answer
rate 0.9583 → 0.5000. **Not adopted.**

## Consequences

- The shipped gate is unchanged: confidence below theta, or the M14 word rule.
- FR-17 stays open, and now with a measured ceiling rather than a hypothesis: two levels of
  vocabulary — words and pairs — both measure phrasing, and phrasing varies in answerable
  questions as much as in unanswerable ones. The next candidate is a semantic check (a
  cross-encoder or judge trained on answerability), which this project's constraints rule out of
  scope. Saying that is the result, not an excuse for a third vocabulary rule.
- `eval/golden_set_v4.jsonl` is frozen and spent. It refused a signal that looked good on paper,
  which is what a decision set is for, and any future refusal claim needs a v5.
- The module (`src/atlas/phrase.py`) is kept, unshipped and tested at the unit level, so the next
  person can re-run the measurement instead of re-deriving it. `results/phrase.md` prints every
  question, both signals and both decisions.

## Alternatives rejected

- **Adopt at a lower floor.** Every floor below 1.00 refuses most answerable questions too
  (0.33: 23 of 24); the grid is printed in full.
- **Count unseen pairs instead of a share.** A count is the same rule with a different denominator
  and cannot separate two classes whose distributions coincide — the report shows the coincidence
  rather than the alternative's number, which is why it was not fitted.
- **Re-tune floor and theta together on v4.** Two knobs on 18 questions, one of which is the
  decision set: that is how a holdout gets spent twice.
