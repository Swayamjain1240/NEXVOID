# Exact cache - hit rate, latency by state, and the leak it must not have (M25)

Command: `python -m bench.cache` · commit `392ae66` · platform: Windows-11-10.0.26300-SP0

Configuration: 256 entries per process, keyed on `(tenant, query, mode, as_of, corpus_hash)`; degraded answers are never stored.

## Hit rate on a repeat mix

240 sequential requests over the 24 frozen answerable questions, drawn Zipf-weighted (question i has weight 1/(i+1)) from a deterministic low-discrepancy sequence. The mix is **invented** - there is no traffic log in this project - so read the hit rate as "what a repeat-heavy workload would get", not as a production forecast.

| requests | hits | misses | hit rate | hit p50 / p95 | miss p50 / p95 |
|---|---|---|---|---|---|
| 240 | 216 | 24 | 90.0% | 1.82 / 17.95 ms | 2.88 / 27.08 ms |

`/v1/metrics` counted 216 hits and 24 misses against the harness's 216 and 24; they agree exactly, and the cache holds 24 entries.

## Latency by cache state

| state | requests | p50 ms | p95 ms |
|---|---|---|---|
| cold: miss (view already warm) | 24 | 2.57 | 5.31 |
| warm: hit | 240 | 1.64 | 16.09 |
| control: cache off | 264 | 2.39 | 18.09 |

A hit costs **1.64 ms** p50 against **2.39 ms** for the same questions with the cache off: **1.5x**, and what remains is serialisation plus the HTTP round trip. The cold row is a miss with the view already built, so the comparison is not borrowing the 44 ms build.

The p50 is the stable comparison; the p95 columns move with this laptop's scheduler (one 20 ms stall sets the tail when a round is only 24 requests), so no tail claim is made from them.

## The tenancy probe

| step | tenant | status | cache | answer |
|---|---|---|---|---|
| first ask | campus | answered | miss | Every student must maintain at least 75 percent attendance in each cou |
| same string, other tenant | rival | answered | miss | Every student must maintain at least 40 percent attendance in each cou |
| first tenant again | campus | answered | hit | Every student must maintain at least 75 percent attendance in each cou |
| other tenant again | rival | answered | hit | Every student must maintain at least 40 percent attendance in each cou |

The two tenants' answers differ: **True**, and each tenant's repeat returns its own answer: **True**. Both tenants were answered. `/v1/metrics` shows 2 hit(s) and 2 miss(es) for the four requests: the second ask per tenant is a hit on its own entry, never on the other's.

## The date is in the key

| as_of | citation in force from | first | repeat | same text |
|---|---|---|---|---|
| 2024-06-01 | (undated) | miss | hit | True |
| 2025-06-01 | 2025-01-01 | miss | hit | True |
| 2026-08-01 | 2026-07-01 | miss | hit | True |

The three dates return **3 distinct answers** from three revisions of the same document, and each repeat is a hit that returns its own date's text. Without `as_of` in the key, the first answer would have been served for the other two.

## Invalidation

| step | cache | answer |
|---|---|---|
| first ask | miss | Every student must maintain at least 75 percent attendance in each course, computed from t |
| repeat, before the edit | hit | Every student must maintain at least 75 percent attendance in each course, computed from t |
| after re-ingesting the document | miss | Every student must maintain at least 51 percent attendance in each course, computed from t |

## Interpretation

- The cache is **per process**: with N workers there are N caches and N hit rates, and `/v1/metrics` describes the one that answered. No number here is an average across processes.
- A hit is a new request: it gets a fresh `trace_id` and reports `retrieve_ms` 0.0, because no retrieval happened. The cached body keeps its citations and confidence.
- A miss pays for the lookup and nothing else: 2.57 ms against 2.39 ms with the cache off is inside run-to-run noise, so the cache is not a tax on the traffic it cannot help.
- The key is the safety mechanism, not a tuning parameter: tenant, date and corpus fingerprint are all in it before any comparison, so a leak needs a key collision, not a lucky question.
- Invalidation is by content: re-ingesting a document changes `corpus_hash`, so old keys stop matching. A running process still has to reload its view to see new documents - the same requirement it had before the cache existed (L9, L20).
- Degraded answers are not stored. They are the fallback for a failed extraction, and caching one would keep serving it after the generator recovered.

