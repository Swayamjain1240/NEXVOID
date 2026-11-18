# Where the evidence for each requirement lives

`tests/test_traceability.py` reads this table and fails if a row points at a file that does not
exist, so nothing here can claim a report the repository does not have. Rows cover requirements
whose evidence is a report rather than a test that names them; the rest are cited directly in the
test or report that demonstrates them.

| id | how it is demonstrated | file |
|---|---|---|
| FR-24 | recall@k, precision@k, MRR and nDCG@10 over the frozen golden set | `results/eval_report.md` |
| FR-25 | one row per retrieval mode, compared on the same questions | `results/ablation.md` |
| FR-26 | corpus size, golden-set size, machine, timestamp and encoder in each header | `results/eval_report.md` |
| FR-27 | the eval gate fails on a recall or refusal regression; the failure is demonstrated, not assumed | `results/ci_gate.md` |
| FR-28 | clean clone: setup, ingest, eval, serve, in one documented sequence | `results/handover.md` |
| NFR-05 | precision@5 (capped) reported beside recall@10 | `results/ablation.md` |
| NFR-07 | 30 minutes at 10 users with zero unhandled exceptions | `results/soak.md` |
| NFR-18 | every reported number produced offline by the hashed embedder, with no provider key | `results/eval_report.md` |
| NFR-19 | each metric in the README names the command that produced it | `README.md` |
| FR-21 | `trace_id` and four stage timings asserted on both GET and POST answers | `tests/test_api_contract.py` |
| FR-22 | `/` serves the demo page: question box, answer area, citations | `tests/test_api_contract.py` |
| NFR-08 | a failing provider returns a valid answer with `degraded=true`, never a 5xx | `tests/test_degraded.py` |
| NFR-12 | layering rules checked against the real import graph | `tests/test_architecture.py` |
| NFR-13 | every SRS id resolved to a test, report, ADR or a row in this file | `tests/test_traceability.py` |
