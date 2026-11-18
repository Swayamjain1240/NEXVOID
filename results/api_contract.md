# The HTTP contract, probed against a live server (M18)

Command: `python -m eval.api_contract` · commit `a42ace5` · corpus `corpus/campus (18 documents, frozen at M1)`

A fresh database is ingested from the corpus, the real server is started on an ephemeral
loopback port, and every documented route is called - including the four that are meant
to fail. `?q=` is the only part of a path elided; row 3 carries the actual question.

| # | method | path | expected | actual | shape | verdict |
|---|---|---|---|---|---|---|
| 1 | GET | `/v1/healthz` | 200 | 200 | ok | PASS |
| 2 | GET | `/health` | 200 | 200 | ok | PASS |
| 3 | GET | `/v1/ask?q=...` | 200 | 200 | ok | PASS |
| 4 | POST | `/v1/ask` | 200 | 200 | ok | PASS |
| 5 | POST | `/v1/ask` | 200 | 200 | ok | PASS |
| 6 | POST | `/v1/ask` | 400 | 400 | ok | PASS |
| 7 | POST | `/v1/ask` | 400 | 400 | ok | PASS |
| 8 | GET | `/v1/ask?q=...` | 400 | 400 | ok | PASS |
| 9 | GET | `/v1/metrics` | 200 | 200 | ok | PASS |
| 10 | POST | `/v1/documents` | 404 | 404 | ok | PASS |
| 11 | PUT | `/v1/ask` | 405 | 405 | ok | PASS |
| 12 | GET | `/v1/nope` | 404 | 404 | ok | PASS |

Requests counted by `GET /v1/metrics` during the run: **12**.
Overall: **PASS**

## What the answer body must carry

Eleven top-level fields, checked on both GET and POST:

```
citations, confidence, degraded, mode, query, refusal_reason, refused, text, timings, trace_id, unseen
```

and four stage timings (`retrieve_ms`, `gate_ms`, `extract_ms`, `total_ms`), which is the
observability requirement (NFR-14) expressed as a contract rather than a screenshot.

## What is deliberately absent

`POST /v1/documents` returns 404. ADR-0012 decides that ingest stays a CLI operation with
idempotent checksums: a write endpoint on a single-node demo with no roles, no rate limit
and no backup is the one surface where this project could do real damage. The absence is
probed, so it is a documented decision instead of a missing feature.

## Reproducing

```
make api-contract
```

The probe writes nothing into `data/` and binds an ephemeral port, so it runs wherever
the tests run, including CI.
