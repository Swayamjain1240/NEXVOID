# Corpus-derived query expansion (M27, FR-17)

Encoder `potion-base-8M`, theta 0.31, novelty floor 0.50, up to 3 added
terms per query term. The expander is a PMI table over the tenant's own chunks:
583 terms kept, built from the token sets the view already holds. Expansion is
applied to the retrieval query only - the gate still reads the question as asked, so an
improvement below is retrieval finding a better passage, not novelty being erased.

**The rules, fixed before the run.** Fit on v1 as the *smallest* threshold that costs v1
neither an answer nor a refusal - the most expansion the tuned set tolerates - decide on
v3, and adopt only if v3 improves and no answerable question is lost on any set.

## Every threshold, every set

| PMI threshold | v1 answer | v1 refusal | v2 answer | v2 refusal | v3 answer | v3 refusal | v4 answer | v4 refusal | mean terms added |
|---|---|---|---|---|---|---|---|---|---|
| 0.0 (off, baseline) | 0.9583 | 0.8333 | 0.9167 | 0.5000 | 1.0000 | 0.6667 | 1.0000 | 0.6667 | 0.00 |
| 0.5 | 1.0000 | 0.3333 | 1.0000 | 0.1667 | 1.0000 | 0.5000 | 1.0000 | 0.3333 | 14.77 |
| 1.0 | 1.0000 | 0.3333 | 1.0000 | 0.1667 | 1.0000 | 0.5000 | 1.0000 | 0.3333 | 12.80 |
| 1.5 | 1.0000 | 0.5000 | 1.0000 | 0.1667 | 1.0000 | 0.5000 | 1.0000 | 0.3333 | 8.80 |
| 2.0 | 1.0000 | 0.5000 | 1.0000 | 0.3333 | 1.0000 | 0.5000 | 1.0000 | 0.5000 | 5.90 |
| 2.5 (fitted) | 1.0000 | 0.8333 | 0.9167 | 0.5000 | 1.0000 | 0.5000 | 1.0000 | 0.6667 | 0.73 |
| 3.0 | 0.9583 | 0.8333 | 0.9167 | 0.5000 | 1.0000 | 0.6667 | 1.0000 | 0.6667 | 0.00 |

## Decision

- **Not adopted** (best candidate 2.5): v3 refusal fell (refusal 0.6667 → 0.5000, answer 1.0000 → 1.0000).
- FR-17 stays open with these numbers attached; the shipped default remains
  `expansion_threshold = 0.0`, so no committed answer changed.

## Per question, before and after the fitted threshold

One row is worth a sixth of refusal accuracy on an appended set. `added` lists the terms
expansion put into the retrieval query; the gate never saw them.

| set | id | answerable | confidence before | after | added terms | refused before | after |
|---|---|---|---|---|---|---|---|
| v3 | v3-001 | yes | 0.3647 | 0.3993 | alone, cancellation, initiated | no | no |
| v3 | v3-002 | yes | 0.4216 | 0.4626 | arithmetic, bound, company | no | no |
| v3 | v3-003 | yes | 0.5055 | 0.5055 | - | no | no |
| v3 | v3-004 | yes | 0.5278 | 0.5278 | - | no | no |
| v3 | v3-005 | yes | 0.4875 | 0.4875 | - | no | no |
| v3 | v3-006 | yes | 0.5808 | 0.5808 | - | no | no |
| v3 | v3-007 | yes | 0.4728 | 0.6198 | audited, considered, disbursement | no | no |
| v3 | v3-008 | yes | 0.6021 | 0.6021 | - | no | no |
| v3 | v3-009 | yes | 0.5646 | 0.5646 | - | no | no |
| v3 | v3-010 | yes | 0.5666 | 0.5666 | - | no | no |
| v3 | v3-011 | yes | 0.5444 | 0.5444 | - | no | no |
| v3 | v3-012 | yes | 0.4437 | 0.4437 | - | no | no |
| v3 | v3-013 | no | 0.2136 | 0.3852 | accessible, archive, book | yes | yes |
| v3 | v3-014 | no | 0.3404 | 0.3404 | - | no | no |
| v3 | v3-015 | no | 0.2903 | 0.3650 | again, among, applicant | yes | yes |
| v3 | v3-016 | no | 0.3048 | 0.4031 | 150, advance, break | yes | no |
| v3 | v3-017 | no | 0.3130 | 0.3899 | bonafide, closed, complete | yes | yes |
| v3 | v3-018 | no | 0.4965 | 0.4965 | - | no | no |
| v4 | v4-001 | yes | 0.4952 | 0.4952 | - | no | no |
| v4 | v4-002 | yes | 0.6042 | 0.6042 | - | no | no |
| v4 | v4-003 | yes | 0.5232 | 0.5232 | - | no | no |
| v4 | v4-004 | yes | 0.3893 | 0.3893 | - | no | no |
| v4 | v4-005 | yes | 0.5897 | 0.5897 | - | no | no |
| v4 | v4-006 | yes | 0.3712 | 0.4688 | 150, accessible, advance, alone, archive, break, cancellation, circulation, initiated | no | no |
| v4 | v4-007 | yes | 0.5032 | 0.4345 | again, among, applicant | no | no |
| v4 | v4-008 | yes | 0.4142 | 0.4451 | 2450, again, among, applicant, appointment, documented | no | no |
| v4 | v4-009 | yes | 0.4890 | 0.4500 | bond, damage, lie | no | no |
| v4 | v4-010 | yes | 0.4350 | 0.4350 | - | no | no |
| v4 | v4-011 | yes | 0.5040 | 0.5040 | - | no | no |
| v4 | v4-012 | yes | 0.5968 | 0.6330 | verification | no | no |
| v4 | v4-013 | no | 0.4202 | 0.3980 | average, graded, mean | no | no |
| v4 | v4-014 | no | 0.3958 | 0.3958 | - | yes | yes |
| v4 | v4-015 | no | 0.3745 | 0.3745 | - | no | no |
| v4 | v4-016 | no | 0.1631 | 0.2694 | 8th, basket, home | yes | yes |
| v4 | v4-017 | no | 0.2549 | 0.2765 | 48 | yes | yes |
| v4 | v4-018 | no | 0.2246 | 0.2246 | - | yes | yes |

## What expansion cannot do here

- **A term the corpus never uses has no neighbours.** PMI needs co-occurrence, so the
  questions that fail because their words are absent (v3-014's parking, v3-018's sports
  scholarship) are exactly the ones expansion cannot touch: there is nothing to expand
  from. The signal is corpus-derived, and that is its ceiling.
- **Co-occurrence is not meaning.** Two terms that share a document are candidates
  whether or not they are related; on a corpus of 18 documents almost everything shares
  one, which is why the threshold has to be fitted rather than chosen.
- **The table is corpus-wide, not date-scoped**, so under `as_of` an expanded query can
  carry a word only a later revision uses (L22). This is one reason the default is off.
