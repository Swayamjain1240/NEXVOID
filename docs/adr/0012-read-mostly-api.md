# ADR-0012 — A read-mostly API, and the surface it actually publishes

**Status:** accepted · **Date:** M18 · **Evidence:** `results/api_contract.md`

## Context

FR-20 was written during design, before the API existed, and listed four endpoints:
`POST /v1/ask`, `POST /v1/documents`, `GET /health`, `GET /metrics`. The server that shipped in M5
exposed `GET /v1/ask`, `GET /v1/healthz` and the demo page — read-only, because the page is a GET
form and nothing needed a body. For twelve milestones nobody reconciled the two, so the SRS
promised a surface the code did not have and the docs kept describing the promised one. A reviewer
would find that in ten minutes; that is exactly the kind of gap this project is supposed to measure
rather than hold.

Two of the four are ordinary gaps with no real decision behind them, and one is a genuine boundary
question, which is why this record exists.

## Decision

**The published surface is:**

| Method | Path | Purpose |
|---|---|---|
| GET | `/` | demo page, no auth |
| GET | `/v1/healthz` | liveness (unchanged; the runbook, CI smoke and bench use it) |
| GET | `/health` | alias of the above, for the name FR-20 promised |
| GET | `/v1/ask?q=…` | the product, unchanged |
| POST | `/v1/ask` | the same answer from `{"query", "mode"}` |
| GET | `/v1/metrics` | process counters and a bounded latency summary |

**`POST /v1/documents` is not offered.** Ingest stays a CLI operation
(`atlas.cli ingest corpus/campus`), with checksums and idempotent replacement already tested. FR-20
is amended accordingly.

The reason is not effort. A write endpoint on this deployment would accept documents from anyone who
holds the API token, into a single SQLite file, with no role model, no rate limiting, no quotas, no
backup and no way to see who wrote what. Everything else in this system is safe to expose precisely
because it cannot change state; that property is worth more than endpoint symmetry. The CLI path
already grants the same capability to the operator, who is the only party that should have it.

Everything else in FR-20 is implemented rather than excused: `POST /v1/ask` (with strict body
validation — unknown fields are a 400, because a misspelled `query` must not answer an empty
question), `GET /health`, and `GET /v1/metrics` (new `src/atlas/metrics.py`: per-route counters and a
1000-request latency window, no request content, per process).

## Consequences

- `results/api_contract.md` probes all twelve behaviours — including the four that are supposed to
  fail — against a live server on an ephemeral port, over a freshly ingested corpus. It is a
  committed artefact, not a claim, and `make api-contract` regenerates it.
- `tests/test_api_contract.py` (11 tests) pins the same shapes, so a drifting response body fails the
  build before the report goes stale.
- Unsupported methods now return `405` with the JSON error envelope; previously the stdlib answered
  with an HTML page, which violated FR-23's "never a stack trace" in letter if not in spirit.
- `GET /v1/metrics` is genuinely per process. With more than one worker these numbers would be
  partial; NFR-03 already fixes the deployment at one node, one process, and the report says so.
- `POST /v1/documents` remains the obvious next capability if this ever gets real users and a role
  model. The tests assert its *absence*, so adding it will be a deliberate, visible change.

## Alternatives rejected

- **Implement `POST /v1/documents` now.** A write path without identity, quotas or backup is a
  liability dressed as completeness.
- **Rename the shipped routes to match the SRS (e.g. `/v1/health` only).** It would break the demo
  page, the runbook, the CI smoke check and the bench harness to satisfy a document written before
  the service existed. Aliases cost three lines.
- **Leave FR-20 unmet and undocumented.** That is the state before this record, and it is the one
  option that makes every other claim in the SRS less believable.
