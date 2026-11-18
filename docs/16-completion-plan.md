# Atlas — completion plan (v1.1 → done)

**Document ID:** ATL-PLAN-002 · **Status:** active — M18 complete at `v1.2`, M19 next · **Applies from:** tag `v1.1`
**Related:** [SRS](07-software-requirements.md) · [Runbook](13-handover-runbook.md) · [ADR register](adr/)

This plan closes the project. "Complete" here means something specific and checkable, not "I ran out
of ideas":

> Every requirement in ATL-SRS-001 either has a committed artefact that measures it, or an accepted
> decision record that amends it with evidence. No requirement is silently unmet, and no document
> describes a system the code does not run.

The system is functionally done (M0–M17, tags `v0.1`…`v1.1`). What remains is **agreement between
the specification, the code and the evidence**: one API mismatch, two unenforced NFRs, and reports
whose numbers predate the last two code changes.

---

## 1. Where the project stands

| Area | State at `v1.1` |
|---|---|
| Retrieval | recall@10 hybrid 0.9167 offline / 0.9583 trained; FR-11 met in its amended one-question form (`results/fusion.md`) |
| Answers | extractive, cited, heading-echo guard; top-1 document correct 20/24 · 12/12 · 11/12 (`results/answer_selection.md`) |
| Refusal | confidence + word novelty; FR-17 **not met** with three measured attempts (`results/support.md`, `results/phrase.md`) |
| Safety | 40/40 injections refused, 0 tenant leaks, 0 PII, 3/3 degraded (`results/safety.md`) |
| Performance | NFR-01 met for interactive traffic; reports predate M15/M16 |
| Ops | dashboard, CI gate, handover proof PASS at `v1.0` |
| Docs | 16 documents, 11 ADRs, 39 committed reports |

### Open items this plan must close

| ID | Gap | Evidence today |
|---|---|---|
| G1 | **FR-20 mismatch.** SRS promises `POST /v1/ask`, `POST /v1/documents`, `GET /health`, `GET /metrics`; the server exposes `GET /v1/ask`, `GET /v1/healthz`, `GET /`. Docs 03/04/09 describe the POST surface. | none — this is the gap |
| G2 | **NFR-12 unenforced.** "pipeline imports no concrete implementation / import linter in CI" — no such test exists. | CI file |
| G3 | **NFR-13 unenforced.** "every FR-* has ≥1 automated test" — asserted in prose, never checked. | CI file |
| G4 | **Stale performance evidence.** bench `63b967f`, latency `2f792d0`, trace earlier still. | `results/*.md` provenance |
| G5 | **Stale UI evidence.** screenshots from 7 Nov, before M15/M16 changed the answer text. | `docs/ui-*.png` |
| G6 | **Report coverage gaps.** `v4` is only in `eval/phrase.py`; `fusion`/`phrase` panels absent from the dashboard, whose README claim is "all of the above". | `scripts/dashboard.py` |
| G7 | **FR-17 open.** Deliberately: needs a semantic answerability model. Not a task in this plan — see §6. |

---

## 2. Milestones

Three phases, each ending in a tag and a one-command check. Nothing here changes retrieval or
answer behaviour, so every committed metric stays valid unless a report is regenerated.

| Phase | Tag | Goal | Exit criterion |
|---|---|---|---|
| **M18** | `v1.2` | The API and the SRS say the same thing | `results/api_contract.md` probes every documented endpoint against the live server; SRS/ADR agree with it |
| **M19** | `v1.3` | The structure claims are enforced, not asserted | CI fails when the import graph or the FR→test map is broken; dashboard covers every report |
| **M20** | `v1.4` | Every number comes from current code | every `results/*.md` provenance commit is at or after the last behaviour commit; screenshots re-captured |

Estimated effort: M18 ≈ 4–6 h, M19 ≈ 3–4 h, M20 ≈ 2 h plus waiting. One focused day each for M18 and
M19 is realistic; M20 is mostly clock time.

---

## 3. M18 — one contract, two artefacts (`v1.2`)

**Decision to take first (recommended):** the read-only API stays read-only. A write endpoint
(`POST /v1/documents`) on a single-node demo with no auth roles, no rate limiting and no backup is
the one surface where a portfolio project can do real damage; ingest stays a CLI operation with
idempotent checksums. FR-20 is amended to the surface below via ADR-0012, and the amendment is
published next to the requirement, exactly as FR-11 was.

| Task | Work | Acceptance |
|---|---|---|
| M18-1 | `POST /v1/ask` — JSON body `{query, mode, k}`, same `Answer` schema, same structured errors | 200 with citations; 400 with `code`/`message` on a malformed body; 405 on the wrong method |
| M18-2 | `GET /v1/metrics` — counters and latency summary from `metrics.py`, documented JSON shape | schema asserted in a test; values non-negative; no PII (log audit already covers logging) |
| M18-3 | `GET /health` as an alias of `/v1/healthz` (keep both; the page and runbook use `/v1/healthz`) | both return the same payload |
| M18-4 | ADR-0012 — "read-mostly API in v1", with the ingest-over-HTTP decision and its reason | ADR committed; SRS FR-20 row amended; docs 03/04/09 annotated where they describe POST |
| M18-5 | `eval/api_contract.py` → `results/api_contract.md` — starts the server, probes every documented endpoint, prints method, path, status, schema check | report regenerated by `make api-contract`; every row PASS |
| M18-6 | Tests: `tests/test_api_contract.py` for bodies, methods, error envelope | ≥6 new tests, no network beyond loopback |

**Risk:** the demo page posts nothing today (it calls GET), so adding POST cannot break it. Keep the
GET path untouched and the page unchanged.

---

## 4. M19 — enforcement and coverage (`v1.3`)

| Task | Work | Acceptance |
|---|---|---|
| M19-1 | `tests/test_architecture.py` — parse imports with `ast`, assert the layering rules from `docs/05` §1 (e.g. `answer.py` imports no concrete retriever; `server.py` imports no store internals) | test fails on a deliberately bad import (add `make arch-demo` or a negative fixture) |
| M19-2 | `tests/test_traceability.py` — read FR/NFR ids from `docs/07`, require each id in a test name or a named report; print the missing list on failure | currently passing list is honest; the two amended ids name their ADR |
| M19-3 | `eval/holdout.py` gains v4, so the report has one four-set table instead of three plus a separate file | `results/holdout.md` has v1–v4 columns; FR-11 line unchanged |
| M19-4 | `scripts/dashboard.py` gains `fusion` and `phrase` panels; regenerate | dashboard contains every `results/*.json` that carries a decision |
| M19-5 | CI: run the two new gates; `make check` grows the architecture test | `.github/workflows/ci.yml` and `Makefile` agree |

**Risk:** the traceability test can become a rubber stamp (any test file mentioning "FR-11" passes).
Keep it honest by requiring the id in a test *name* or a report *filename*, and by listing the ids
that only have a report — currently FR-17 and FR-11.

---

## 5. M20 — evidence on current code (`v1.4`)

| Task | Work | Acceptance |
|---|---|---|
| M20-1 | Re-run `make bench`, `make latency`, `make trace`, `make soak-interactive` (skip the 30-minute saturation soak; it measures a load nobody serves) | provenance commit of each report ≥ M17 code commit |
| M20-2 | Regenerate the dashboard from the refreshed JSON | no panel shows a number absent from its source report |
| M20-3 | Re-capture `docs/ui-answer.png` and `docs/ui-refusal.png` from the running server | the answer screenshot shows the 75% rule, not a topic list |
| M20-4 | Update README performance rows and any runbook receipt that moved | numbers match the reports; `git grep` finds no superseded figure |
| M20-5 | Re-run the handover proof at HEAD; tag `v1.4` | `results/handover.md` PASS with the current baseline |

---

## 6. Explicitly out of scope

- **FR-17 (semantic refusal).** Three vocabulary attempts are measured and refused; the next
  candidate is an answerability cross-encoder, which needs a model dependency this project's
  constraints (offline, one runtime dependency) exclude. Closing it is a different project, and
  saying so is the honest end of this line. It stays open in the SRS with its evidence.
- **`POST /v1/documents`.** Amended in M18 rather than built (§3).
- **Multi-node, containers, ANN storage, auth roles.** Permanent non-goals (ATL-PLAN-001 §2).
- **A v5 question set.** Only needed if someone reopens FR-17.

---

## 7. Definition of done (the checklist to hand over)

A reviewer should be able to tick every line:

1. `make ci` green from a clean clone; `make handover` PASS at the release tag.
2. Every id in `docs/07` appears in `tests/test_traceability.py` output as test-backed or
   report-backed; the two amended ones name their ADR.
3. `git grep "POST /v1/documents"` finds only the ADR and the amendment, never an unqualified claim.
4. Every `results/*.md` header names a command that still runs and a commit at or after the last
   behaviour change.
5. The dashboard contains every decision-bearing report, and its numbers equal those reports'.
6. The demo page, the screenshots and the live server return the same answer for the same question.
7. `docs/13` limitations match the SRS: FR-11 amended, FR-17 open, everything else met.
8. No `TODO`, no unadopted experiment described as shipped, no metric without a report.

---

## 8. Risks specific to finishing

| Risk | Probability | Impact | Mitigation |
|---|---|---|---|
| API work grows into auth/roles | Medium | High | ADR-0012 fixes the scope at read-mostly before any code |
| Traceability test becomes a rubber stamp | Medium | Medium | ids must appear in test names or report filenames; the report lists the exceptions |
| Perf re-run moves a headline (e.g. p95) | Medium | Medium | report the delta, do not re-tune; a slower number on current code is still the true number |
| Report sweep tempts a "quick improvement" | Low | High | M18–M20 are evidence phases: no retrieval, ranking or gate changes, no new tuning |
| Screenshots drift again after capture | Low | Low | capture last, in M20, after all code phases are tagged |
