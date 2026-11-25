# Deliberate overload — the bound under real concurrency (M23, NFR-01)

Command: `python -m bench.overload` · commit `0727170` · platform: Windows-11-10.0.26300-SP0

Configuration: **4 slots**, 8 waiters, 25 ms of waiting, 40 ms of padding per answered request, 50 client processes offering 40 requests each. The bound is set below the offered load on purpose, and the padding is applied by this harness around the answer function, not by the server.

| metric | value |
|---|---|
| requests offered | 2000 |
| served (200) | 321 |
| shed (503 OVERLOADED) | 1679 |
| **shed rate** | **83.95%** |
| served p50 / p95 / p99 | 54.06 / 79.05 / 111.77 ms |
| shed p95 | 37.02 ms |
| throughput of served work | 58.4 rps |
| transport failures | 0 |
| admission rejected (from `/v1/metrics`) | 1679 |

## What a caller sees

With every slot held: `/v1/ask` answered **503 OVERLOADED** in 30.05 ms with `Retry-After: 1`, while `/v1/healthz` answered **200** in 15.91 ms. That is the whole design: the refused request is cheap, and the two endpoints an operator needs during an overload are never shed.

## Interpretation

- Shedding is **bounded, not instant**: a refused request pays the configured wait (up to 25 ms) before the 503, which is why shed p95 is 37.02 ms rather than ~1 ms, against served p95 79.05 ms. `ATLAS_ADMISSION_WAIT_MS=0` refuses without waiting, and `tests/test_admission.py` covers that path.
- The served p95 (79.05 ms) is above the harness's 40 ms padding because an admitted request may also wait up to 25 ms for a slot. That wait is bounded, which is the difference between a queue and a bound.
- The 84.0% shed rate is **configured**, not a production result: the shipped bound is 32 slots and shed nothing at the measured loads. `docs/18` M23 set the abandon condition at ~2% shedding to hold the target; this run is the far side of that line, which is why the defaults are not these.
- Admission counted 1679 rejections against the harness's 1679 observed 503s. They agree exactly: liveness and metrics are admitted by design, so they appear on neither side of that comparison.

- Client processes, one keep-alive connection each, real `GET /v1/ask` through the production handler: the same harness as `bench/sweep.py`, so the numbers are comparable with `results/bench.md`.

