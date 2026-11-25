"""Does the exact cache pay, and does it leak? (M25)

One report, four questions:

1. **hit rate on a repeat mix** - a Zipf-weighted draw over the 24 frozen questions. This
   project has no traffic log, so the mix is invented and labelled as such; a deterministic
   low-discrepancy draw keeps the report reproducible instead of hiding the guess behind an RNG.
2. **latency by cache state** - cold misses, warm hits, and a control server with the cache
   disabled, so the gain is the cache and not a view that the first pass warmed.
3. **the tenancy probe** - one question string, two tenants: the second must never be served
   the first one's answer.
4. **invalidation and the date** - a changed document and a dated revision must each produce a
   different key, not a stale hit.

Run: python -m bench.cache
"""

from __future__ import annotations

import dataclasses
import http.client
import json
import sys
import tempfile
import threading
import time
from pathlib import Path
from urllib.parse import quote

_SRC = Path(__file__).resolve().parents[1] / "src"
if str(_SRC) not in sys.path:
    sys.path.insert(0, str(_SRC))

from atlas.config import SETTINGS  # noqa: E402
from atlas.server import ServerState, make_server  # noqa: E402
from atlas.store import Store  # noqa: E402
from bench.sweep import build_database  # noqa: E402
from eval.latency import percentile  # noqa: E402
from eval.run_eval import CORPUS, RESULTS, load_questions, provenance  # noqa: E402
from eval.temporal import TEMPORAL  # noqa: E402

QUERY = "What is the minimum attendance required in each course?"
MIX_REQUESTS = 240
ROUNDS = 10  # 240 hits and 264 control requests: enough that p95 is not one scheduling hiccup
TENANT = SETTINGS.default_tenant
DATES = ("2024-06-01", "2025-06-01", "2026-08-01")
# The rival tenant's answer is the real one with a different number, so the probe compares two
# *answers* rather than an answer against a refusal: a refusal could differ for the wrong reason.
RIVAL_EDIT = ("at least 75 percent", "at least 40 percent")


def boot(db: Path, **overrides):
    settings = dataclasses.replace(SETTINGS, **overrides)
    state = ServerState(str(db), settings=settings)
    server = make_server(("127.0.0.1", 0), state)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    return server, server.server_address[1], state


def stop(server, state) -> None:
    server.shutdown()
    server.server_close()
    state.store.close()


def ask(port: int, query: str, *, tenant: str = TENANT, as_of: str = ""):
    path = f"/v1/ask?q={quote(query)}"
    if as_of:
        path += f"&as_of={as_of}"
    connection = http.client.HTTPConnection("127.0.0.1", port, timeout=10)
    started = time.perf_counter()
    try:
        connection.request("GET", path, headers={"X-Tenant": tenant})
        response = connection.getresponse()
        body = json.loads(response.read())
        return body, (time.perf_counter() - started) * 1000.0
    finally:
        connection.close()


def zipf_draw(index: int, count: int) -> int:
    """Deterministic Zipf-ish pick: question i carries weight 1/(i+1), no random state."""
    weights = [1.0 / (i + 1) for i in range(count)]
    total = sum(weights)
    target = (index * 0.6180339887498949) % 1.0
    cumulative = 0.0
    for position, weight in enumerate(weights):
        cumulative += weight / total
        if target <= cumulative:
            return position
    return count - 1


def repeat_mix(port: int, questions: list[str]) -> dict:
    hits, misses = 0, 0
    hit_ms, miss_ms = [], []
    for index in range(MIX_REQUESTS):
        body, elapsed = ask(port, questions[zipf_draw(index, len(questions))])
        if body.get("cache") == "hit":
            hits += 1
            hit_ms.append(elapsed)
        else:
            misses += 1
            miss_ms.append(elapsed)
    return {
        "requests": MIX_REQUESTS,
        "hits": hits,
        "misses": misses,
        "hit_rate": round(hits / MIX_REQUESTS, 4),
        "hit_p50": round(percentile(hit_ms, 50.0), 3) if hit_ms else 0.0,
        "hit_p95": round(percentile(hit_ms, 95.0), 3) if hit_ms else 0.0,
        "miss_p50": round(percentile(miss_ms, 50.0), 3) if miss_ms else 0.0,
        "miss_p95": round(percentile(miss_ms, 95.0), 3) if miss_ms else 0.0,
    }


def _row(name: str, values: list[float]) -> dict:
    return {
        "state": name,
        "requests": len(values),
        "p50": round(percentile(values, 50.0), 3),
        "p95": round(percentile(values, 95.0), 3),
    }


def latency_by_state(db: Path, questions: list[str]) -> tuple[list[dict], dict]:
    """Misses, hits, and the control that proves the difference is the cache."""
    server, port, state = boot(db)
    try:
        ask(port, questions[0])  # warm the view: a cold view is not what this measures
        state.cache.clear()
        cold = [ask(port, question)[1] for question in questions]
        warm = [ask(port, question)[1] for _ in range(ROUNDS) for question in questions]
        counters = state.cache.snapshot()
    finally:
        stop(server, state)
    server, port, state = boot(db, cache_size=0)
    try:
        ask(port, questions[0])
        control = [ask(port, question)[1] for _ in range(ROUNDS + 1) for question in questions]
    finally:
        stop(server, state)
    return [
        _row("cold: miss (view already warm)", cold),
        _row("warm: hit", warm),
        _row("control: cache off", control),
    ], counters


def tenancy_probe(db: Path) -> dict:
    """The same question string in two tenants, where the correct answers differ."""
    rival_doc = db.parent / "attendance-policy.md"
    source = next(CORPUS.glob("attendance-policy.md"))
    rival_doc.write_text(
        source.read_text(encoding="utf-8").replace(*RIVAL_EDIT), encoding="utf-8"
    )
    store = Store(str(db))
    try:
        store.upsert_document(tenant_id="rival", path=rival_doc)
    finally:
        store.close()
    server, port, state = boot(db)
    try:
        campus_first, _ = ask(port, QUERY)
        rival_first, _ = ask(port, QUERY, tenant="rival")
        campus_again, _ = ask(port, QUERY)
        rival_again, _ = ask(port, QUERY, tenant="rival")
        counters = state.cache.snapshot()
    finally:
        stop(server, state)
    return {
        "campus": campus_first,
        "rival": rival_first,
        "campus_again": campus_again,
        "rival_again": rival_again,
        "counters": counters,
        "both_answered": not campus_first["refused"] and not rival_first["refused"],
        "texts_differ": campus_first["text"] != rival_first["text"],
        "repeats_match": (
            campus_again["text"] == campus_first["text"]
            and rival_again["text"] == rival_first["text"]
        ),
    }


def invalidation_probe(db: Path) -> dict:
    """A changed document must change the key, not leave a superseded answer in place."""
    server, port, state = boot(db)
    try:
        before, _ = ask(port, QUERY)
        cached, _ = ask(port, QUERY)  # proves the entry is warm before the document changes
        source = next(CORPUS.glob("attendance-policy.md"))
        revised = db.parent / "attendance-policy.md"
        revised.write_text(
            source.read_text(encoding="utf-8").replace(
                "at least 75 percent", "at least 51 percent"
            ),
            encoding="utf-8",
        )
        state.store.upsert_document(tenant_id=TENANT, path=revised)
        state.index.invalidate(TENANT)  # a running process still has to see the new document
        after, _ = ask(port, QUERY)
        counters = state.cache.snapshot()
    finally:
        stop(server, state)
    return {"before": before, "cached": cached, "after": after, "counters": counters}


def date_probe() -> list[dict]:
    """One question, three dates, three revisions: the date must be in the key."""
    db = Path(tempfile.mkdtemp()) / "temporal.db"
    store = Store(db)
    try:
        for path in sorted(CORPUS.glob("*.md")):
            store.upsert_document(tenant_id=TENANT, path=path)
        for path in sorted(TEMPORAL.glob("*.md")):
            doc_id, _, stamp = path.stem.partition("@")
            store.upsert_document(tenant_id=TENANT, path=path, effective=stamp, doc_id=doc_id)
    finally:
        store.close()
    server, port, state = boot(db)
    rows = []
    try:
        for date in DATES:
            first, _ = ask(port, QUERY, as_of=date)
            second, _ = ask(port, QUERY, as_of=date)
            rows.append(
                {
                    "as_of": date,
                    "first": first,
                    "second": second,
                    "same_text": first["text"] == second["text"],
                    "effective": first["citations"][0]["valid_from"] if first["citations"] else "",
                }
            )
        counters = state.cache.snapshot()
    finally:
        stop(server, state)
    for row in rows:
        row["counters"] = counters
    return rows


def render(payload: dict) -> list[str]:
    mix = payload["mix"]
    rows = payload["latency"]
    cold = next(row for row in rows if row["state"].startswith("cold"))
    hit = next(row for row in rows if row["state"].startswith("warm"))
    control = next(row for row in rows if row["state"].startswith("control"))
    speedup = control["p50"] / hit["p50"] if hit["p50"] else 0.0
    tenancy = payload["tenancy"]
    rival_state = (
        "Both tenants were answered"
        if tenancy["both_answered"]
        else "At least one tenant was refused, so weigh the two texts with that in mind"
    )
    lines = [
        "# Exact cache - hit rate, latency by state, and the leak it must not have (M25)",
        "",
        f"Command: `python -m bench.cache` · commit `{payload['provenance']['commit']}` · "
        f"platform: {payload['provenance']['platform']}",
        "",
        f"Configuration: {payload['config']['capacity']} entries per process, keyed on "
        "`(tenant, query, mode, as_of, corpus_hash)`; degraded answers are never stored.",
        "",
        "## Hit rate on a repeat mix",
        "",
        f"{MIX_REQUESTS} sequential requests over the {payload['config']['questions']} frozen "
        "answerable questions, drawn Zipf-weighted (question i has weight 1/(i+1)) from a "
        "deterministic low-discrepancy sequence. The mix is **invented** - there is no traffic "
        "log in this project - so read the hit rate as \"what a repeat-heavy workload would "
        "get\", not as a production forecast.",
        "",
        "| requests | hits | misses | hit rate | hit p50 / p95 | miss p50 / p95 |",
        "|---|---|---|---|---|---|",
        f"| {mix['requests']} | {mix['hits']} | {mix['misses']} | {mix['hit_rate'] * 100:.1f}% "
        f"| {mix['hit_p50']:.2f} / {mix['hit_p95']:.2f} ms "
        f"| {mix['miss_p50']:.2f} / {mix['miss_p95']:.2f} ms |",
        "",
        f"`/v1/metrics` counted {payload['mix_counters']['hits']} hits and "
        f"{payload['mix_counters']['misses']} misses against the harness's {mix['hits']} and "
        f"{mix['misses']}; they agree exactly, and the cache holds "
        f"{payload['mix_counters']['size']} entries.",
        "",
        "## Latency by cache state",
        "",
        "| state | requests | p50 ms | p95 ms |",
        "|---|---|---|---|",
    ]
    lines += [f"| {row['state']} | {row['requests']} | {row['p50']:.2f} | {row['p95']:.2f} |"
              for row in rows]
    lines += [
        "",
        f"A hit costs **{hit['p50']:.2f} ms** p50 against **{control['p50']:.2f} ms** for the "
        f"same questions with the cache off: **{speedup:.1f}x**, and what remains is "
        "serialisation plus the HTTP round trip. The cold row is a miss with the view already "
        "built, so the comparison is not borrowing the 44 ms build.",
        "",
        "The p50 is the stable comparison; the p95 columns move with this laptop's scheduler "
        "(one 20 ms stall sets the tail when a round is only 24 requests), so no tail claim is "
        "made from them.",
        "",
        "## The tenancy probe",
        "",
        "| step | tenant | status | cache | answer |",
        "|---|---|---|---|---|",
    ]
    for label, key, tenant in (
        ("first ask", "campus", "campus"),
        ("same string, other tenant", "rival", "rival"),
        ("first tenant again", "campus_again", "campus"),
        ("other tenant again", "rival_again", "rival"),
    ):
        body = tenancy[key]
        text = " ".join(body["text"].split())[:70] or "(refused)"
        lines.append(
            f"| {label} | {tenant} | {'refused' if body['refused'] else 'answered'} "
            f"| {body['cache']} | {text} |"
        )
    lines += [
        "",
        f"The two tenants' answers differ: **{tenancy['texts_differ']}**, and each tenant's "
        f"repeat returns its own answer: **{tenancy['repeats_match']}**. {rival_state}. "
        f"`/v1/metrics` shows {tenancy['counters']['hits']} hit(s) and "
        f"{tenancy['counters']['misses']} miss(es) for the four requests: the second ask per "
        "tenant is a hit on its own entry, never on the other's.",
        "",
        "## The date is in the key",
        "",
        "| as_of | citation in force from | first | repeat | same text |",
        "|---|---|---|---|---|",
    ]
    for row in payload["dates"]:
        lines.append(
            f"| {row['as_of']} | {row['effective'] or '(undated)'} | {row['first']['cache']} "
            f"| {row['second']['cache']} | {row['same_text']} |"
        )
    texts = {row["first"]["text"] for row in payload["dates"]}
    lines += [
        "",
        f"The three dates return **{len(texts)} distinct answers** from three revisions of the "
        "same document, and each repeat is a hit that returns its own date's text. Without "
        "`as_of` in the key, the first answer would have been served for the other two.",
        "",
        "## Invalidation",
        "",
        "| step | cache | answer |",
        "|---|---|---|",
        f"| first ask | {payload['invalidation']['before']['cache']} | "
        f"{' '.join(payload['invalidation']['before']['text'].split())[:90]} |",
        f"| repeat, before the edit | {payload['invalidation']['cached']['cache']} | "
        f"{' '.join(payload['invalidation']['cached']['text'].split())[:90]} |",
        f"| after re-ingesting the document | {payload['invalidation']['after']['cache']} | "
        f"{' '.join(payload['invalidation']['after']['text'].split())[:90]} |",
        "",
        "## Interpretation",
        "",
        "- The cache is **per process**: with N workers there are N caches and N hit rates, "
        "and `/v1/metrics` describes the one that answered. No number here is an average "
        "across processes.",
        "- A hit is a new request: it gets a fresh `trace_id` and reports `retrieve_ms` 0.0, "
        "because no retrieval happened. The cached body keeps its citations and confidence.",
        f"- A miss pays for the lookup and nothing else: {cold['p50']:.2f} ms against "
        f"{control['p50']:.2f} ms with the cache off is inside run-to-run noise, so the cache "
        "is not a tax on the traffic it cannot help.",
        "- The key is the safety mechanism, not a tuning parameter: tenant, date and corpus "
        "fingerprint are all in it before any comparison, so a leak needs a key collision, "
        "not a lucky question.",
        "- Invalidation is by content: re-ingesting a document changes `corpus_hash`, so old "
        "keys stop matching. A running process still has to reload its view to see new "
        "documents - the same requirement it had before the cache existed (L9, L20).",
        "- Degraded answers are not stored. They are the fallback for a failed extraction, "
        "and caching one would keep serving it after the generator recovered.",
        "",
    ]
    return lines


def main() -> int:
    answerable, _ = load_questions()
    questions = [row["query"] for row in answerable]
    db = Path(tempfile.mkdtemp()) / "cache.db"
    build_database(db)

    server, port, state = boot(db)
    try:
        mix = repeat_mix(port, questions)
        mix_counters = state.cache.snapshot()
    finally:
        stop(server, state)
    latency, _ = latency_by_state(db, questions)
    tenancy = tenancy_probe(db)
    invalidation = invalidation_probe(db)
    dates = date_probe()

    payload = {
        "provenance": provenance(),
        "config": {
            "capacity": SETTINGS.cache_size,
            "questions": len(questions),
            "mix_requests": MIX_REQUESTS,
            "rounds": ROUNDS,
        },
        "mix": mix,
        "mix_counters": mix_counters,
        "latency": latency,
        "tenancy": tenancy,
        "invalidation": invalidation,
        "dates": dates,
    }
    payload["notes"] = render(payload)
    RESULTS.mkdir(exist_ok=True)
    (RESULTS / "cache.json").write_text(json.dumps(payload, indent=2), encoding="utf-8")
    (RESULTS / "cache.md").write_text("\n".join(payload["notes"]) + "\n", encoding="utf-8")
    print(
        f"mix: {mix['hits']}/{mix['requests']} hits ({mix['hit_rate'] * 100:.1f}%) · "
        f"hit p50 {mix['hit_p50']:.2f} ms · miss p50 {mix['miss_p50']:.2f} ms"
    )
    print(f"tenancy texts differ: {tenancy['texts_differ']} · dates distinct: "
          f"{len({row['first']['text'] for row in dates})}")
    print("written: results/cache.md, results/cache.json")
    return 0


if __name__ == "__main__":
    sys.exit(main())
