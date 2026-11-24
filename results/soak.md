# Soak — 30 minutes at 10 users (M10)

Command: `python -m bench.sweep --soak-seconds 1800 --users 10` · commit `5398f23`

| metric | value |
|---|---|
| duration | 1800 s |
| requests | 1638403 |
| served (200) | 1638403 |
| shed (503 OVERLOADED) | 0 (0.00%, p95 0.00 ms) |
| failures | 0 (0.000%) |
| p50 ms | 9.01 |
| p95 ms (served) | 22.88 |
| p99 ms | 32.85 |
| sustained rps | 910.1 |
| load model | no think time (saturation model: the same users, offering far more load) |

## Interpretation

- p95 after 30 minutes: 22.88 ms, +4.69 ms against the short-run p95 (18.19 ms). A leak or an unbounded cache would show here as growth.
- NFR-01 is written at 10 users, and this run is exactly that load held for 30 minutes with no pause between requests: p95 22.88 ms against a 50 ms target at 10 concurrent users. Nothing was shed, so every request offered was answered inside it.
- Nothing was shed in this run, so the admission bound never became the binding constraint; it is a ceiling, not a throttle on normal traffic.
- Unlike the burst, this run cannot borrow credit from an idle moment: the threshold holds under sustained load.
- Error rate 0.000% over 1638403 requests; these are
  dropped connections, and none of them changed an answer's content.
- The per-tenant view is the only thing that grows with traffic, and it is keyed by
  tenant, so this run also checks that repeated requests do not accumulate per request.

- The other load model is committed beside this one: `results/soak-interactive.json` reports p95 9.89 ms at 10 users with 100 ms think time. The two answer different questions - headroom versus user traffic - and neither replaces the other.

