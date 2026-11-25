# Cold start, query cost and ingest cost at 34 / 1k / 10k chunks (M26, NFR-03)

Command: `python -m bench.scale` · commit `03eccbf` · platform: Windows-11-10.0.26300-SP0

| chunks | documents | ingest s | cold start ms | warm p50 ms | warm p95 ms | rebuild ms | incremental ingest ms |
|---|---|---|---|---|---|---|---|
| 34 | 18 | 0.06 | 36.48 | 0.06 | 0.08 | 35.63 | 15.48 |
| 1053 | 351 | 3.65 | 1438.52 | 2.82 | 3.36 | 1356.52 | 43.62 |
| 10053 | 3351 | 35.41 | 10906.80 | 27.49 | 44.67 | 11228.11 | 453.48 |

## What the columns mean

- **ingest s** — writing the corpus through `Store.upsert_document`, one commit per document, with WAL on. This is the CLI's job, not the request path.
- **cold start ms** — the first `search()` after opening the store: BM25, the vector matrix, the token caches and the ranker features, built together.
- **warm p50 / p95 ms** — 20 queries after that build. This is what a request pays.
- **rebuild ms** — `invalidate()` then the next query, i.e. what an ingest used to cost when the only way to see a new document was to drop the view.
- **incremental ingest ms** — appending 5 documents (15 chunks) to a built view via `refresh()`, which extends instead of rebuilding.

## Interpretation

- Query cost grows with the corpus, as an exact scan must: p50 0.06 → 27.49 ms from 34 to 10053 chunks. The 34-chunk figure sits at the edge of timer resolution, so read the growth between the two larger levels, where it is close to linear. That is the arithmetic an ANN index would have to beat, and at these numbers it does not earn its complexity — `docs/18` M26 keeps ANN out until a curve like this one says otherwise.
- Cold start is the expensive one: 10907 ms at 10053 chunks, paid once per process per tenant. It is linear in chunks because every chunk is tokenised twice (BM25 and the vector projector) and embedded once.
- An ingest into a warm view costs **453 ms** at 10053 chunks against **11228 ms** for the rebuild it replaces (24.8x). The append is O(new chunks) plus BM25's idf sweep over the vocabulary; the rebuild is O(corpus).
- The append reuses every vector already computed, which is also its limit: hashed TF-IDF bakes corpus idf into each row, so appending a document leaves the old rows on the idf they were built with. `tests/test_incremental.py` asserts that this does not reorder a result on the mini corpus, and L21 records that it is an approximation rather than an exactness proof.
- The synthetic levels are generated text over a 300-word vocabulary: good for tokenising and scoring cost, meaningless for answer quality. Quality claims stay on the frozen sets.

- A change that is not an append still rebuilds: a revised document, a closed revision window or a deleted chunk cannot be patched into a posting list, so `refresh()` drops the view and builds it again. The report measures the case that can be extended, because that is the one M26 changes.

