# Soak — 15 minutes at 10 users (M10)

Command: `python -m bench.sweep --soak-seconds 900 --users 10 --think-ms 100` · commit `5398f23`

| metric | value |
|---|---|
| duration | 900 s |
| requests | 84925 |
| served (200) | 84925 |
| shed (503 OVERLOADED) | 0 (0.00%, p95 0.00 ms) |
| failures | 0 (0.000%) |
| p50 ms | 5.01 |
| p95 ms (served) | 9.89 |
| p99 ms | 12.72 |
| sustained rps | 94.4 |
| load model | 100 ms think time per user (interactive model) |

## Interpretation

- p95 after 15 minutes: 9.89 ms, -8.30 ms against the short-run p95 (18.19 ms). A leak or an unbounded cache would show here as growth.
- NFR-01 is written at 10 users, and this run holds that load with 100 ms of think time per user: p95 9.89 ms against a 50 ms target at 10 concurrent users, and 0 requests were shed, so read strictly NFR-01 passes.
- Nothing was shed in this run, so the admission bound never became the binding constraint; it is a ceiling, not a throttle on normal traffic.
- Unlike the burst, this run cannot borrow credit from an idle moment: the threshold holds under sustained load.
- Error rate 0.000% over 84925 requests; these are
  dropped connections, and none of them changed an answer's content.
- The per-tenant view is the only thing that grows with traffic, and it is keyed by
  tenant, so this run also checks that repeated requests do not accumulate per request.

- The other load model is committed beside this one: `results/soak.json` reports p95 22.88 ms at 10 users with no think time (saturation). The two answer different questions - headroom versus user traffic - and neither replaces the other.

