"""Cold start, query cost and ingest cost as the corpus grows (M26, NFR-03).

Two numbers per level that must never be added together: what the first query costs (the view
build, paid once) and what a warm query costs (retrieval, paid every time). Then the number
this phase exists for: what an ingest costs when the view is extended instead of rebuilt.

The real corpus is 18 documents / 34 chunks, so the 1k and 10k levels are **synthetic**:
deterministic generated policy text over a 300-word vocabulary, because NFR-03 is a claim
about ~10k chunks and the project does not have them. No number here is a quality claim - the
synthetic text is there to be tokenised, not to be answered.

Run: python -m bench.scale
"""

from __future__ import annotations

import json
import random
import sys
import tempfile
import time
from pathlib import Path

_SRC = Path(__file__).resolve().parents[1] / "src"
if str(_SRC) not in sys.path:
    sys.path.insert(0, str(_SRC))

from atlas.config import SETTINGS  # noqa: E402
from atlas.retrieval import HybridIndex  # noqa: E402
from atlas.store import Store  # noqa: E402
from bench.sweep import build_database  # noqa: E402
from eval.latency import percentile  # noqa: E402
from eval.run_eval import RESULTS, provenance  # noqa: E402

TENANT = SETTINGS.default_tenant
LEVELS = (34, 1000, 10000)
GROW_DOCS = 5  # the ingest the incremental path is measured on
SECTIONS = 6
WORDS_PER_SECTION = 55
SYLLABLES = ("ka", "lo", "mi", "nu", "pe", "ri", "so", "ta", "vu", "ze")
VOCAB = [a + b + c for a in SYLLABLES for b in SYLLABLES for c in ("n", "r", "s")]
WEIGHTS = [1.0 / (position + 1) for position in range(len(VOCAB))]
QUERIES = [
    " ".join(random.Random(seed).choices(VOCAB[:40], k=3)) for seed in range(20)
]


def synthetic_doc(number: int) -> str:
    """One generated policy document: `SECTIONS` paragraphs of `WORDS_PER_SECTION` words."""
    rng = random.Random(number)
    parts = [f"# Synthetic Policy {number}", ""]
    for section in range(SECTIONS):
        words = rng.choices(VOCAB, weights=WEIGHTS, k=WORDS_PER_SECTION)
        parts += [f"## Section {section}", "", " ".join(words) + ".", ""]
    return "\n".join(parts)


def grow(store: Store, folder: Path, start: int, count: int) -> tuple[int, float]:
    """Ingest `count` generated documents through the production path; return chunks and seconds."""
    started = time.perf_counter()
    chunks = 0
    for number in range(start, start + count):
        path = folder / f"synthetic-{number:05d}.md"
        path.write_text(synthetic_doc(number), encoding="utf-8")
        chunks += store.upsert_document(tenant_id=TENANT, path=path)
    return chunks, time.perf_counter() - started


def timed_search(index: HybridIndex, query: str) -> float:
    started = time.perf_counter()
    index.search(query, tenant_id=TENANT, k=5)
    return (time.perf_counter() - started) * 1000.0


def measure(target: int, folder: Path) -> dict:
    """One corpus size: ingest, cold start, warm queries, rebuild, and one incremental ingest."""
    db = folder / f"scale-{target}.db"
    if target == LEVELS[0]:
        started = time.perf_counter()
        build_database(db)  # the real corpus, through the same ingest path
        ingest_s = time.perf_counter() - started
    else:
        started = time.perf_counter()
        documents = 0
        writer = Store(db)
        try:
            while writer.stats(tenant_id=TENANT)["chunks"] < target:
                batch = 1 if documents == 0 else 50
                grow(writer, folder, documents, batch)
                documents += batch
        finally:
            writer.close()
        ingest_s = time.perf_counter() - started
    store = Store(db)
    try:
        counts = store.stats(tenant_id=TENANT)
        documents, chunks = counts["documents"], counts["chunks"]

        index = HybridIndex(store)
        cold_ms = timed_search(index, QUERIES[0])
        warm = [timed_search(index, query) for query in QUERIES]

        started = time.perf_counter()
        index.invalidate(TENANT)
        rebuild_ms = (time.perf_counter() - started) * 1000.0 + timed_search(index, QUERIES[0])

        added, ingest_grow_s = grow(store, folder, documents, GROW_DOCS)
        started = time.perf_counter()
        mode = index.refresh(TENANT)
        incremental_ms = (time.perf_counter() - started) * 1000.0
        after = store.stats(tenant_id=TENANT)["chunks"]
    finally:
        store.close()
    return {
        "target": target,
        "chunks": chunks,
        "documents": documents,
        "ingest_s": round(ingest_s, 2),
        "cold_ms": round(cold_ms, 2),
        "warm_p50": round(percentile(warm, 50.0), 2),
        "warm_p95": round(percentile(warm, 95.0), 2),
        "rebuild_ms": round(rebuild_ms, 2),
        "grow_chunks": added,
        "grow_ingest_s": round(ingest_grow_s, 3),
        "incremental_ms": round(incremental_ms, 2),
        "refresh_mode": mode,
        "chunks_after": after,
    }


def render(rows: list[dict], payload: dict) -> list[str]:
    first, last = rows[0], rows[-1]
    grown = sorted({row["grow_chunks"] for row in rows})
    grown_range = str(grown[0]) if len(grown) == 1 else f"{grown[0]}-{grown[-1]}"
    lines = [
        "# Cold start, query cost and ingest cost at 34 / 1k / 10k chunks (M26, NFR-03)",
        "",
        f"Command: `python -m bench.scale` · commit `{payload['provenance']['commit']}` · "
        f"platform: {payload['provenance']['platform']}",
        "",
        "| chunks | documents | ingest s | cold start ms | warm p50 ms | warm p95 ms "
        "| rebuild ms | incremental ingest ms |",
        "|---|---|---|---|---|---|---|---|",
    ]
    lines += [
        f"| {row['chunks']} | {row['documents']} | {row['ingest_s']:.2f} | {row['cold_ms']:.2f} "
        f"| {row['warm_p50']:.2f} | {row['warm_p95']:.2f} | {row['rebuild_ms']:.2f} "
        f"| {row['incremental_ms']:.2f} |"
        for row in rows
    ]
    lines += [
        "",
        "## What the columns mean",
        "",
        "- **ingest s** — writing the corpus through `Store.upsert_document`, one commit per "
        "document, with WAL on. This is the CLI's job, not the request path.",
        "- **cold start ms** — the first `search()` after opening the store: BM25, the vector "
        "matrix, the token caches and the ranker features, built together.",
        "- **warm p50 / p95 ms** — 20 queries after that build. This is what a request pays.",
        "- **rebuild ms** — `invalidate()` then the next query, i.e. what an ingest used to cost "
        "when the only way to see a new document was to drop the view.",
        "- **incremental ingest ms** — appending "
        f"{GROW_DOCS} documents ({grown_range} chunks) to a built view via `refresh()`, "
        "which extends instead of rebuilding.",
        "",
        "## Interpretation",
        "",
        f"- Query cost grows with the corpus, as an exact scan must: p50 "
        f"{first['warm_p50']:.2f} → {last['warm_p50']:.2f} ms from {first['chunks']} to "
        f"{last['chunks']} chunks. The 34-chunk figure sits at the edge of timer resolution, "
        "so read the growth between the two larger levels, where it is close to linear. That "
        "is the arithmetic an ANN index would have to beat, and at these numbers it does not "
        "earn its complexity — `docs/18` M26 keeps ANN out until a curve like this one says "
        "otherwise.",
        f"- Cold start is the expensive one: {last['cold_ms']:.0f} ms at {last['chunks']} "
        "chunks, paid once per process per tenant. It is linear in chunks because every chunk "
        "is tokenised twice (BM25 and the vector projector) and embedded once.",
        f"- An ingest into a warm view costs **{last['incremental_ms']:.0f} ms** at "
        f"{last['chunks']} chunks against **{last['rebuild_ms']:.0f} ms** for the rebuild it "
        f"replaces ({last['rebuild_ms'] / max(last['incremental_ms'], 0.01):.1f}x). The append "
        "is O(new chunks) plus BM25's idf sweep over the vocabulary; the rebuild is O(corpus).",
        "- The append reuses every vector already computed, which is also its limit: hashed "
        "TF-IDF bakes corpus idf into each row, so appending a document leaves the old rows on "
        "the idf they were built with. `tests/test_incremental.py` asserts that this does not "
        "reorder a result on the mini corpus, and L21 records that it is an approximation "
        "rather than an exactness proof.",
        "- The synthetic levels are generated text over a 300-word vocabulary: good for "
        "tokenising and scoring cost, meaningless for answer quality. Quality claims stay on "
        "the frozen sets.",
        "",
        "- A change that is not an append still rebuilds: a revised document, a closed revision "
        "window or a deleted chunk cannot be patched into a posting list, so `refresh()` drops "
        "the view and builds it again. The report measures the case that can be extended, "
        "because that is the one M26 changes.",
        "",
    ]
    return lines


def main() -> int:
    folder = Path(tempfile.mkdtemp())
    rows = [measure(target, folder) for target in LEVELS]
    payload = {"provenance": provenance(), "rows": rows, "grow_docs": GROW_DOCS}
    payload["notes"] = render(rows, payload)
    RESULTS.mkdir(exist_ok=True)
    (RESULTS / "scale.json").write_text(json.dumps(payload, indent=2), encoding="utf-8")
    (RESULTS / "scale.md").write_text("\n".join(payload["notes"]) + "\n", encoding="utf-8")
    for row in rows:
        print(
            f"  {row['chunks']:>6} chunks: cold {row['cold_ms']:.1f} ms · warm p50 "
            f"{row['warm_p50']:.2f} ms · rebuild {row['rebuild_ms']:.0f} ms · append "
            f"{row['incremental_ms']:.1f} ms ({row['refresh_mode']})"
        )
    print("written: results/scale.md, results/scale.json")
    return 0


if __name__ == "__main__":
    sys.exit(main())
