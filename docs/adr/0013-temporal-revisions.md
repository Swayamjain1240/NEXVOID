# ADR-0013 — Revision windows on chunks, and answers as of a date

**Status:** accepted (M21, v1.5.0) · **Supersedes:** nothing · **Affects:** `store`, `schema`,
`versions`, `retrieval`, `answer`, `api`, `cli`

## Context

Every earlier milestone answered one question: *what does the corpus say now*. For policy
content that is the wrong question half the time. A circular superseded in July is still in
the shared drive, and an assistant that quotes the 2026 rule for a 2023 enrolment is worse than
one that refuses, because it produces a **cited** answer that is confidently anachronistic.
Nothing in the system could represent "this rule took effect on this date", so the failure was
not detectable, let alone measurable.

Two shapes were available: version the *documents* (a row per document version) or version the
*chunks* (a window per passage). Chunks were chosen because citations are chunk-level: a
citation must resolve to the text that was in force, not to a document whose current contents
have moved on.

## Decision

1. **A chunk carries a closed window**: `valid_from`, `valid_to`, both ISO dates, both `''` when
   unbounded. ISO strings compare lexicographically, so `in_force` is two string comparisons;
   `''` sorts before every date, which makes "no recorded effective date" mean "in force at
   every date" without a sentinel such as `0001-01-01` that invites arithmetic on it.
2. **Ingest appends, it does not overwrite.** A dated ingest closes the open revision the day
   before it takes effect (`valid_to = effective - 1 day`) and inserts its own chunks with
   `valid_to = ''`. The superseded text stays queryable, which is the entire point.
3. **Back-dating is an error.** Ingesting a revision older than an open one raises
   `InputError` instead of re-closing windows: silently rewriting history would change answers
   already given, and there is no way to know which callers saw them.
4. **The date filter runs inside each retriever, before fusion.** `BM25Index.search` and the
   cosine search both take an allowed-index set, so an out-of-force revision cannot occupy a
   candidate slot, cannot collect a fusion vote, and cannot be reranked into the answer.
   Filtering the fused top-k afterwards was rejected: it lets a superseded chunk displace the
   in-force one and then removes it, which is a silent recall loss rather than a filter.
5. **A new refusal reason, `not_yet_in_effect`.** When nothing in force clears the gate but the
   same retrieval without the date filter produces a passing passage dated after `as_of`, the
   refusal names that date in `effective_from`. "I don't know" and "that rule does not exist
   yet" are different facts, and a policy assistant that conflates them is unusable at a
   counter.
6. **Novelty is scoped to the revisions in force.** The vocabulary signal from M14 uses the
   token set of in-force chunks for a dated query, so a term introduced in 2026 cannot make a
   2024 answer look grounded.

## Consequences

- `chunks` gains two columns; a pre-M21 database is migrated in place with both defaulting to
  `''` (`schema.migrate`), which preserves its old behaviour exactly.
- Chunk ids include the effective date, so two revisions of one document cannot share an id;
  a citation to the 2025 rule resolves to the 2025 text.
- `corpus/temporal/` holds six authored revisions of three policies (two each), because the
  frozen campus corpus is deliberately undated. The revisions tighten one number and add one
  new fact per document, which is what makes a genuine "not yet in effect" question possible.
- `make temporal` writes `results/temporal.md`: 9/9 version accuracy, 3/3 not-yet refusals with
  the right date, and a **0.0000 future-leak rate** at both retrieval and answer level, against
  a control showing 4 of 9 dated questions would have cited a not-yet-effective revision.
- Cost: every retrieval now carries one optional set membership test per candidate; the
  temporal run adds no measurable latency to the undated path.

## Alternatives rejected

| Alternative | Why not |
|---|---|
| Keep only the current text, delete on update | Cannot answer a question about a past date at all; the failure this milestone exists to fix |
| A separate `document_versions` table keyed by doc | Citations are chunk-level in this system (FR-14); a document-level version cannot say which passage was in force |
| Filter the fused top-k after ranking | Changes recall silently instead of filtering, and lets an out-of-force chunk win a fusion vote (ADR-0010's failure mode, reintroduced) |
| One index per date, built on demand | Correct but rebuilds the embedding matrix per date; the mask is O(1) per candidate and measured at 0 leaks |
| Coerce unknown dates (`2025-13-01` → `2025-12-01`) | An answer about the wrong policy, delivered confidently; a 400 is the honest response |
| A sentinel effective date (`0001-01-01`) for undated documents | Invites arithmetic and comparisons that treat it as a real date; `''` states the absence instead |
