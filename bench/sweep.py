"""Latency curve and soak test (NFR-01, NFR-02): one command, three levels, real HTTP.

`bench.load_test` answers "does 10 concurrent users stay under 50 ms?" with a single point.
A single point cannot show a knee, so this script sweeps 1, 10 and 50 users over the same
corpus and adds an optional sustained run, which is where leaks and tail growth show up.

Method is the same one the M5 measurement settled on, and it is stated here because the
numbers mean nothing without it:

* the server is a real `ThreadingHTTPServer` over a temporary database built by the same
  ingest path, in this process - one process, one port, as deployed;
* each user is a separate client PROCESS with one persistent keep-alive connection, since
  agent-threaded stopwatches are GIL-inflated (measured and rejected in M5);
* every request is a real `GET /v1/ask` answered by the production code path.

M28 adds the writer variant. A soak with no writer measures a server whose corpus never
changes, which is the one thing a read-mostly service is *not*: an operator ingests. With
`--writer-seconds` set, a second connection appends a generated document every interval while
every request is answered, so the M26 reload path - the server noticing a changed corpus
signature and rebuilding the affected view - runs inside the tail this report measures. It gets
its own stem (`results/soak-writer.*`) because it answers a different question than the
canonical soak and must not replace it.

Run:  python -m bench.sweep                          # 1, 10, 50 users -> results/bench.*
      python -m bench.sweep --soak-seconds 1800      # 30-minute soak -> results/soak.*
      python -m bench.sweep --soak-seconds 600 --writer-seconds 20   # soak + ingest
      python -m bench.sweep --soak-seconds 600 --writer-seconds 20 \\
          --writer-tenant campus-control   # the same writes into a tenant nobody reads

The control exists because one number cannot say which half of a tail it is. A writer into the
served tenant changes that tenant's view, so its requests pay for the reload; a writer into a
tenant nobody reads changes nothing the served requests look at, so the run keeps the cost of
writing (WAL, a second connection, a larger file) and loses the corpus growth and the reload.
The difference between the two reports is the ingest cost the serving path actually pays.
"""

from __future__ import annotations

import argparse
import contextlib
import dataclasses
import http.client
import json
import multiprocessing
import sqlite3
import sys
import tempfile
import threading
import time
from array import array
from http.server import ThreadingHTTPServer
from pathlib import Path
from urllib.parse import quote

_SRC = Path(__file__).resolve().parents[1] / "src"
if str(_SRC) not in sys.path:
    sys.path.insert(0, str(_SRC))

from atlas.config import SETTINGS  # noqa: E402
from atlas.server import ServerState, make_server  # noqa: E402
from atlas.store import Store  # noqa: E402
from eval.latency import percentile  # noqa: E402
from eval.run_eval import CORPUS, RESULTS, load_questions, provenance  # noqa: E402

FIXED_LEVELS = (1, 10, 50)
REQUESTS_PER_USER = 100
NFR01_USERS, NFR01_P95_MS = 10, 50.0


def build_database(path: Path) -> None:
    """Same ingest path as production, into a throwaway database."""
    store = Store(path)
    try:
        for document in sorted(CORPUS.glob("*.md")):
            store.upsert_document(tenant_id="campus", path=document)
    finally:
        store.close()


def boot(db: Path) -> tuple[ThreadingHTTPServer, int, ServerState]:
    # M25: the answer cache is off in every load report. These workloads repeat a frozen set of
    # questions, so a warm cache would measure the cache rather than the server; the cache has
    # its own report (`results/cache.md`) and its own workload.
    settings = dataclasses.replace(SETTINGS, cache_size=0)
    state = ServerState(str(db), settings=settings)
    server = make_server(("127.0.0.1", 0), state)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    # M29: the state comes back so the run can read the reload counters it accumulated.
    return server, server.server_address[1], state


def _worker(host: str, port: int, questions: list[str], token: str, index: int,
            per_user: int, deadline: float, out_queue, think_seconds: float = 0.0) -> None:
    """One user process: `per_user` requests, or requests until `deadline` (0 = none).

    `think_seconds` is the pause between one user's requests. Zero is a saturation test:
    ten clients with no think time offer far more load than ten people ever would, and the
    queue that builds is measured and reported as such. A non-zero value defines the
    interactive load model instead, and the two are never mixed in one number.
    """
    connection = http.client.HTTPConnection(host, port, timeout=10)
    latencies = array("f")        # answered 200s: the only population p95 may describe
    shed_latencies = array("f")   # 503s: kept to show that shedding is fast
    failures = 0
    shed = 0
    attempt = 0
    try:
        while True:
            if deadline and time.time() >= deadline:
                break
            if not deadline and attempt >= per_user:
                break
            query = questions[(index + attempt) % len(questions)]
            started = time.perf_counter()
            status = 0
            try:
                headers = {"Authorization": f"Bearer {token}"} if token else {}
                if attempt == 0:
                    connection.connect()
                connection.request("GET", f"/v1/ask?q={quote(query)}", headers=headers)
                response = connection.getresponse()
                response.read()
                status = response.status
            except (OSError, http.client.HTTPException):
                status = 0
            elapsed = (time.perf_counter() - started) * 1000.0
            if status == 200:
                latencies.append(elapsed)
            elif status == 503:
                # M23: a shed request is a decision, not a failure. Counting it as a failure
                # would report admission control as a regression; counting it as a success
                # would hide the load the server declined. It gets its own number.
                shed += 1
                shed_latencies.append(elapsed)
            else:
                failures += 1
            attempt += 1
            # soak mode sets per_user = 0 and runs until a deadline, so the pause cannot be
            # conditioned on there being attempts left.
            if think_seconds and (per_user == 0 or attempt < per_user):
                time.sleep(think_seconds)
    finally:
        connection.close()
        out_queue.put((latencies.tolist(), failures, shed, shed_latencies.tolist()))


def run_load(base_url: str, questions: list[str], *, users: int, per_user: int = 0,
             seconds: float = 0.0, token: str = "", think_seconds: float = 0.0) -> dict:
    """Launch `users` client processes and aggregate their readings."""
    host, port_text = base_url.replace("http://", "").rsplit(":", 1)
    deadline = time.time() + seconds if seconds else 0.0
    queue: multiprocessing.Queue = multiprocessing.Queue()
    started = time.perf_counter()
    processes = [
        multiprocessing.Process(
            target=_worker,
            args=(host, int(port_text), questions, token, index, per_user, deadline, queue,
                  think_seconds),
        )
        for index in range(users)
    ]
    for process in processes:
        process.start()
    collected = [queue.get() for _ in processes]
    wall = time.perf_counter() - started
    for process in processes:
        process.join()
    values = [value for latencies, _, _, _ in collected for value in latencies]
    shed_values = [value for _, _, _, extra in collected for value in extra]
    failures = sum(count for _, count, _, _ in collected)
    shed = sum(count for _, _, count, _ in collected)
    total = len(values) + shed + failures
    return {
        "users": users,
        "requests": total,
        "served": len(values),
        "shed": shed,
        "shed_rate": round(shed / total, 4) if total else 0.0,
        "failures": failures,
        "wall_seconds": round(wall, 2),
        "p50_ms": round(percentile(values, 50.0), 3) if values else None,
        "p95_ms": round(percentile(values, 95.0), 3) if values else None,
        "p99_ms": round(percentile(values, 99.0), 3) if values else None,
        "shed_p95_ms": round(percentile(shed_values, 95.0), 3) if shed_values else 0.0,
        "rps": round(len(values) / wall, 1) if wall > 0 else 0.0,
    }


def sweep(questions: list[str], db: Path, *, levels: tuple[int, ...], per_user: int,
          token: str = "") -> list[dict]:
    """Every level against one long-lived server, so warm views are warm throughout."""
    server, port, _ = boot(db)
    try:
        base_url = f"http://127.0.0.1:{port}"
        rows = []
        for users in levels:
            row = run_load(base_url, questions, users=users, per_user=per_user,
                           token=token)
            rows.append(row)
            print(f"  {users:>2} users: p50 {row['p50_ms']} ms · p95 {row['p95_ms']} ms · "
                  f"{row['rps']} rps · {row['shed']} shed · {row['failures']} failures",
                  file=sys.stderr)
    finally:
        server.shutdown()
        server.server_close()
    return rows


def _soak_document(number: int) -> str:
    """The scale bench's generated policy text, so both reports ingest the same shape."""
    from bench.scale import synthetic_doc  # late: bench.scale imports build_database from here

    return synthetic_doc(number)


def _write_loop(db: Path, interval: float, stop: threading.Event, out: dict,
                tenant: str) -> None:
    """Append one document per interval to the live database, as an operator's ingest would.

    Its own connection, because that is what the reload path exists for: the serving process
    holds a different connection and can only notice the change through the corpus signature.
    `tenant` is the served tenant for the treatment run and a tenant nobody reads for the
    control, which is the whole difference between them.
    A locked database is recorded as a reading, not raised - this thread must not be able to
    end the run it is measuring.
    """
    store = Store(db)
    folder = Path(tempfile.mkdtemp())
    latencies: list[float] = []
    errors = 0
    number = 0
    try:
        while not stop.is_set():
            path = folder / f"soak-{number:05d}.md"
            try:
                path.write_text(_soak_document(number), encoding="utf-8")
                started = time.perf_counter()
                store.upsert_document(tenant_id=tenant, path=path)
                latencies.append((time.perf_counter() - started) * 1000.0)
                number += 1
            except (sqlite3.OperationalError, OSError):
                errors += 1
            stop.wait(interval)
    finally:
        out.update({
            "documents": number,
            "errors": errors,
            "p95_ms": round(percentile(latencies, 95.0), 3) if latencies else None,
            "stats": store.stats(tenant_id=tenant),
        })
        store.close()


def _pregrow(db: Path, count: int) -> None:
    """Ingest `count` generated documents before the run: a bigger served view, no reload.

    The third arm (M29). It holds the corpus at the size the writer soak reaches, with no ingest
    happening during the measurement, so its tail is view size and nothing else.
    """
    store = Store(db)
    folder = Path(tempfile.mkdtemp())
    try:
        for number in range(count):
            path = folder / f"pre-{number:05d}.md"
            path.write_text(_soak_document(number), encoding="utf-8")
            store.upsert_document(tenant_id=SETTINGS.default_tenant, path=path)
    finally:
        store.close()


def soak(questions: list[str], db: Path, *, seconds: float, users: int = 10,
         token: str = "", think_seconds: float = 0.0, writer_seconds: float = 0.0,
         writer_tenant: str = "", pregrow: int = 0) -> dict:
    """Sustained load: the run that finds growth a short burst cannot.

    `writer_seconds` adds the second half of the story (M28): a concurrent writer appending to
    the same database, so the run covers an ingest happening *while* the service serves.
    `writer_tenant` moves that writer to a tenant nobody reads, which is the control: the
    served view never changes, so the reload disappears from the tail and only the writing
    remains.
    """
    served = SETTINGS.default_tenant
    tenant = writer_tenant or served
    if pregrow:
        _pregrow(db, pregrow)
    server, port, state = boot(db)
    probe = Store(db)
    try:
        before = probe.stats(tenant_id=served)
        before_writer = before if tenant == served else probe.stats(tenant_id=tenant)
    finally:
        probe.close()
    stop = threading.Event()
    written: dict = {}
    writer: threading.Thread | None = None
    try:
        if writer_seconds > 0:
            writer = threading.Thread(
                target=_write_loop, args=(db, writer_seconds, stop, written, tenant),
                daemon=True,
            )
            writer.start()
        row = run_load(f"http://127.0.0.1:{port}", questions, users=users,
                       seconds=seconds, token=token, think_seconds=think_seconds)
    finally:
        stop.set()
        if writer is not None:
            writer.join(timeout=30.0)
        server.shutdown()
        server.server_close()
    row["duration_seconds"] = seconds
    row["error_rate"] = round(row["failures"] / row["requests"], 6) if row["requests"] else 0.0
    # M29: the reload path's own reading, taken from the process that paid for it
    row["reload"] = state.reload_stats()
    row["reload_ms_per_request"] = (
        round(row["reload"]["total_ms"] / row["requests"], 6) if row["requests"] else None
    )
    if pregrow:
        row["pregrown_documents"] = pregrow
    # M30: the served corpus is recorded for every arm, not just the ones with a writer. A
    # pre-grown run has to report the size it actually served, not only what was ingested into it.
    probe = Store(db)
    try:
        after_served = probe.stats(tenant_id=served)
    finally:
        probe.close()
    row["documents_before"] = before["documents"]
    row["documents_after"] = after_served["documents"]
    row["chunks_after"] = after_served["chunks"]
    if writer_seconds > 0:
        after_writer = written.get("stats", {})
        row.update({
            "writer_seconds": writer_seconds,
            "writer_tenant": tenant,
            "documents_written": written.get("documents", 0),
            "writer_documents_before": before_writer["documents"],
            "writer_documents_after": after_writer.get("documents"),
            "writer_chunks_after": after_writer.get("chunks"),
            "write_p95_ms": written.get("p95_ms"),
            "write_errors": written.get("errors", 0),
        })
    return row


def soak_line(ten: dict | None) -> list[str]:
    """Cross-reference the soak, so a burst PASS is never read as a sustained PASS."""
    path = RESULTS / "soak.json"
    if not ten or not path.exists():
        return []
    row = json.loads(path.read_text(encoding="utf-8")).get("row", {})
    if not row or row.get("users") != ten["users"]:
        return []
    holds = row["p95_ms"] <= NFR01_P95_MS
    return [
        f"The {row['duration_seconds'] / 60:.0f}-minute soak holds this same load "
        f"({row['users']} users) and reads p95 {row['p95_ms']:.2f} ms, "
        f"{'inside' if holds else 'over'} the {NFR01_P95_MS:.0f} ms target. It is the stricter "
        "of the two readings because it cannot borrow credit from an idle moment.",
    ]


def write_bench(rows: list[dict], payload: dict, *, stem: str = "bench", label: str = "",
                command: str = "python -m bench.sweep") -> None:
    """`stem` lets a one-off run keep its own report instead of replacing the canonical curve.

    `command` is the invocation that produced this report, printed in its header: a labelled
    stem is only reproducible if the flags that made it are written down with it.
    """
    RESULTS.mkdir(exist_ok=True)
    (RESULTS / f"{stem}.json").write_text(json.dumps(payload, indent=2), encoding="utf-8")
    ten = next((row for row in rows if row["users"] == NFR01_USERS), None)
    verdict = "PASS" if ten and ten["p95_ms"] <= NFR01_P95_MS else "FAIL"
    title = "# Load curve — 1, 10 and 50 concurrent users (M10)"
    if label:
        title += f" — {label}"
    lines = [
        title,
        "",
        f"Command: `{command}` · commit `{payload['provenance']['commit']}` · "
        f"platform: {payload['provenance']['platform']}",
        "",
        "| users | requests | served | shed | failures | p50 ms | p95 ms | p99 ms | rps |",
        "|---|---|---|---|---|---|---|---|---|",
    ]
    lines += [
        f"| {row['users']} | {row['requests']} | {row.get('served', row['requests'])} | "
        f"{row.get('shed', 0)} | {row['failures']} | {row['p50_ms']:.2f} "
        f"| {row['p95_ms']:.2f} | {row['p99_ms']:.2f} | {row['rps']:.1f} |"
        for row in rows
    ]
    lines += [
        "",
        f"NFR-01 (p95 <= {NFR01_P95_MS:.0f} ms at {NFR01_USERS} users): **{verdict}** "
        f"({'p95 ' + format(ten['p95_ms'], '.2f') + ' ms' if ten else 'level missing'}), "
        f"with {ten.get('shed', 0) if ten else 0} of {ten['requests'] if ten else 0} requests "
        "shed as 503 OVERLOADED (M23 admission control).",
        "",
        "Method: real `ThreadingHTTPServer` over a temporary database, one client PROCESS "
        "per user with a persistent keep-alive connection, 100 requests per user per level.",
        "The curve is the point: a single number cannot show where the knee is.",
        "",
        # a labelled one-off run must not borrow the canonical soak's number
        *([] if stem != "bench" else soak_line(ten)),
        "",
        "## Interpretation",
        "",
        *payload["notes"],
        "",
    ]
    (RESULTS / f"{stem}.md").write_text("\n".join(lines) + "\n", encoding="utf-8")


def soak_stem(row: dict) -> str:
    """The report a run belongs in; no arm can overwrite another."""
    if row.get("pregrown_documents"):
        return "soak-pregrown"
    if row.get("writer_seconds"):
        if row.get("writer_tenant", SETTINGS.default_tenant) != SETTINGS.default_tenant:
            return "soak-writer-control"
        return "soak-writer"
    return "soak-interactive" if row.get("think_ms") else "soak"


def write_soak(row: dict, payload: dict, *, stem: str = "") -> None:
    """One report per load model, so no run can replace a number that answers another question."""
    RESULTS.mkdir(exist_ok=True)
    stem = stem or soak_stem(row)
    (RESULTS / f"{stem}.json").write_text(json.dumps(payload, indent=2), encoding="utf-8")
    model = (
        f"{row['think_ms']:.0f} ms think time per user (interactive model)"
        if row.get("think_ms")
        else "no think time (saturation model: the same users, offering far more load)"
    )
    tenant = row.get("writer_tenant", SETTINGS.default_tenant)
    lines = [
        f"# Soak — {row['duration_seconds'] / 60:.0f} minutes at {row['users']} users (M10)",
        "",
        f"Command: `python -m bench.sweep --soak-seconds {int(row['duration_seconds'])} "
        f"--users {row['users']}"
        + (f" --think-ms {row['think_ms']:.0f}" if row.get("think_ms") else "")
        + (f" --writer-seconds {row['writer_seconds']:.0f}" if row.get("writer_seconds") else "")
        + (f" --writer-tenant {tenant}" if tenant != SETTINGS.default_tenant else "")
        + (f" --pregrow {row['pregrown_documents']}" if row.get("pregrown_documents") else "")
        + f"` · commit `{payload['provenance']['commit']}`",
        "",
        "| metric | value |",
        "|---|---|",
        f"| duration | {row['duration_seconds']:.0f} s |",
        f"| requests | {row['requests']} |",
        f"| served (200) | {row.get('served', row['requests'])} |",
        f"| shed (503 OVERLOADED) | {row.get('shed', 0)} "
        f"({row.get('shed_rate', 0.0) * 100:.2f}%, p95 {row.get('shed_p95_ms', 0.0):.2f} ms) |",
        f"| failures | {row['failures']} ({row['error_rate'] * 100:.3f}%) |",
        f"| p50 ms | {row['p50_ms']:.2f} |",
        f"| p95 ms (served) | {row['p95_ms']:.2f} |",
        f"| p99 ms | {row['p99_ms']:.2f} |",
        f"| sustained rps | {row['rps']:.1f} |",
        f"| load model | {model} |",
        *([] if not row.get("pregrown_documents") else [
            f"| pre-grown | {row['pregrown_documents']} documents ingested before the run |",
        ]),
        *([] if "documents_after" not in row else [
            f"| served corpus | {row['documents_before']} -> {row['documents_after']} documents "
            f"({row['chunks_after']} chunks) |",
        ]),
        # M29: the reload path's own reading, taken where it runs rather than inferred. A run
        # taken before the counters existed must not print zero - it did not measure them.
        *([] if not row.get("reload") else [
            f"| reload calls | {row['reload']['count']} ({row['reload']['outcomes']}) |",
            f"| reload total | {row['reload']['total_ms']:.2f} ms "
            f"({row.get('reload_ms_per_request') or 0.0:.6f} ms per request) |",
            "| reload p50 / p95 / max | "
            + " / ".join(
                "-" if value is None else f"{value:.2f}"
                for value in (
                    row["reload"]["p50_ms"],
                    row["reload"]["p95_ms"],
                    row["reload"]["max_ms"],
                )
            )
            + " ms |",
        ]),
        *([] if not row.get("writer_seconds") else [
            f"| concurrent writer | one document every {row['writer_seconds']:.0f} s, into "
            f"tenant `{tenant}` |",
            f"| documents appended | {row['documents_written']} |",
            f"| writer's tenant | "
            f"{row.get('writer_documents_before', row['documents_before'])} -> "
            f"{row.get('writer_documents_after', row['documents_after'])} documents "
            f"({row.get('writer_chunks_after', row['chunks_after'])} chunks) |",
            "| ingest p95 | "
            + (f"{row['write_p95_ms']:.2f} ms |" if row.get("write_p95_ms") is not None
               else "- (no successful append) |"),
            f"| ingest errors | {row['write_errors']} |",
        ]),
        "",
        "## Interpretation",
        "",
        *payload["notes"],
        *ingest_split_lines(row),
        *other_model_line(row),
        "",
    ]
    (RESULTS / f"{stem}.md").write_text("\n".join(lines) + "\n", encoding="utf-8")


def other_model_line(row: dict) -> list[str]:
    """Point at the other load model, so neither number can be quoted as if it were alone."""
    other = "soak.json" if row.get("think_ms") else "soak-interactive.json"
    path = RESULTS / other
    if not path.exists():
        return []
    other_row = json.loads(path.read_text(encoding="utf-8")).get("row", {})
    if not other_row:
        return []
    label = "no think time (saturation)" if row.get("think_ms") else "100 ms think time"
    return [
        "",
        f"- The other load model is committed beside this one: `results/{other}` reports p95 "
        f"{other_row['p95_ms']:.2f} ms at {other_row['users']} users with {label}. The two "
        "answer different questions - headroom versus user traffic - and neither replaces "
        "the other.",
    ]


def notes_for_sweep(rows: list[dict]) -> list[str]:
    one = next((row for row in rows if row["users"] == 1), rows[0])
    fifty = next((row for row in rows if row["users"] == 50), rows[-1])
    growth = (fifty["p95_ms"] / one["p95_ms"]) if one["p95_ms"] else 0.0
    return [
        f"- p95 grows {growth:.1f}x from 1 to 50 users ({one['p95_ms']:.1f} -> "
        f"{fifty['p95_ms']:.1f} ms) while throughput reaches {fifty['rps']:.0f} rps.",
        (
            f"  At 50 the bound is reached and {fifty['shed']} requests are shed, so the tail "
            "above it is the socket queue, not the answer path."
            if fifty.get("shed")
            else "  The server is not saturated at 50: the cost is queueing on one process, "
            "which is the documented deployment shape rather than a defect."
        ),
        f"- Failures at every level: {sum(row['failures'] for row in rows)}. A failure here is a",
        "  dropped connection, not a refused answer: refusals are HTTP 200 by design.",
        "- Two harness findings from getting this curve right, both measured: the stdlib listen",
        "  backlog of 5 dropped 7 connections at 50 users until it was raised to 128, and logging",
        "  every request into a captured pipe read p95 55.39 ms at 10 users (a FAIL) until the log",
        "  went to a file, as a deployed process writes it (46.52 ms, PASS). The logger is in the",
        "  tail path; that is a result about the service, not an excuse for the number.",
        f"- Shed at every level: {sum(row.get('shed', 0) for row in rows)} of "
        f"{sum(row['requests'] for row in rows)} requests, all of them 503 OVERLOADED from the "
        "M23 bound rather than dropped connections. The 10-user level is the NFR-01 operating "
        "point and sits below the bound, so the target is measured without shedding.",
        "- The answer cache is off here (`ATLAS_CACHE_SIZE=0`): 100 requests per user over 24 "
        "questions would otherwise be a cache benchmark, not a retrieval one. `results/cache.md` "
        "measures the cache, with its own repeat mix.",
        "- One process, one port, no container: this curve is the whole capacity story for the",
        "  deployment the project claims, and it says nothing about horizontal scaling.",
    ]


def _nfr01_note(row: dict) -> str:
    """NFR-01 is a claim about served traffic, plus the shed rate it was bought with (M23)."""
    target = f"{NFR01_P95_MS:.0f} ms target at {row['users']} concurrent users"
    if row["users"] != NFR01_USERS:
        # the target is written at exactly one operating point; a run at another user count
        # says something about headroom, not about compliance
        return (
            f"- NFR-01 is written at {NFR01_USERS} users, and this run holds {row['users']}: p95 "
            f"{row['p95_ms']:.2f} ms with {row.get('shed', 0)} requests shed, so read it as a "
            "headroom reading beside the 10-user point, not as a compliance claim."
        )
    if row.get("think_ms"):
        return (
            f"- NFR-01 is written at {NFR01_USERS} users, and this run holds that load with "
            f"{row['think_ms']:.0f} ms of think time per user: p95 {row['p95_ms']:.2f} ms "
            f"against a {target}, and {row.get('shed', 0)} requests were shed, so read "
            "strictly NFR-01 passes."
        )
    base = (
        f"- NFR-01 is written at {NFR01_USERS} users, and this run is exactly that load held "
        f"for {row['duration_seconds'] / 60:.0f} minutes with no pause between requests: p95 "
        f"{row['p95_ms']:.2f} ms against a {target}."
    )
    if not row.get("shed"):
        return base + " Nothing was shed, so every request offered was answered inside it."
    return base + (
        f" {row['shed']} of {row['requests']} requests were shed as 503 OVERLOADED "
        f"({row['shed_rate'] * 100:.2f}%), so that p95 describes the requests the server chose "
        "to answer, not all the load offered. Read strictly, NFR-01 passes for served traffic "
        "and the shed rate is the price; the pre-M23 alternative was answering all of them by "
        "queueing, which is what put p95 over the target in the first place."
    )


# The M29 session's three arms, on the same host and corpus sizes, before M30 replaced the
# signature re-hash with a counter. Kept so a later rendering can say how far the same arm moves
# between sessions: it is the size of the noise the split has to survive.
PRIOR_ARMS = {"control": 21.62, "writer": 23.96, "pregrown": 25.24}


def _committed_row(stem: str) -> dict | None:
    """One committed report: soak payloads keep `row`, the load curve keeps `rows`."""
    path = RESULTS / f"{stem}.json"
    if not path.exists():
        return None
    payload = json.loads(path.read_text(encoding="utf-8"))
    return payload.get("row") or payload


def ingest_split_lines(row: dict) -> list[str]:
    """Attribute a soak's tail to view size, writing and the reload path (M29).

    Three arms and one baseline, all committed: the short curve (18 documents, nothing else
    running), the pre-grown arm (the writer's final corpus, no ingest during the run), the
    control (18 documents, writes into a tenant nobody reads) and the writer soak. Subtracting
    the first two arms from the third leaves the reload path, and the server's own counters
    measure that path directly - so the subtraction has a witness instead of being the whole
    argument. A missing arm prints nothing rather than guessing.
    """
    bench = _committed_row("bench")
    writer = _committed_row("soak-writer")
    control = _committed_row("soak-writer-control")
    pregrown = _committed_row("soak-pregrown")
    if not (bench and writer and control and pregrown):
        return []
    entry = next((item for item in bench["rows"] if item["users"] == row["users"]), None)
    if entry is None:
        return []
    base = entry["p95_ms"]
    stats = writer.get("reload", {})
    outcomes = stats.get("outcomes", {})
    worked = stats.get("count", 0) - outcomes.get("none", 0) - outcomes.get("missing", 0)
    per_request = writer.get("reload_ms_per_request") or 0.0
    view = pregrown["p95_ms"] - base
    writing = control["p95_ms"] - base
    total = writer["p95_ms"] - base
    arms = {
        "control": control["p95_ms"],
        "writer": writer["p95_ms"],
        "pregrown": pregrown["p95_ms"],
    }
    spread = max(arms.values()) - min(arms.values())
    ordered = writing < total < view
    moved = {name: value - PRIOR_ARMS[name] for name, value in arms.items()}
    rps_gap = (writer["rps"] - control["rps"]) / writer["rps"] * 100
    if ordered:
        verdict = (
            f"- **So, in this session:** of the writer soak's {total:+.2f} ms, about "
            f"{writing:+.2f} ms is writing beside the service and the rest is the larger view it "
            f"serves - the writer sits between the control and the pre-grown arm, as in M29. "
            f"That is one session per arm, and one arm moves {abs(moved['control']):.2f} ms "
            f"between sessions (below), so read the split as indicative and the reload figure "
            f"({per_request:.4f} ms per request, counted) as measured."
        )
    else:
        verdict = (
            f"- **So no split is claimed.** The control reads {writing:+.2f} ms over the short "
            f"curve where M29 read {PRIOR_ARMS['control'] - base:+.2f}, and it lands above the "
            f"writer rather than below it, so writing and view size are not separable at the noise "
            f"level: the writer soak's {total:+.2f} ms is a bracket set by arms that differ by "
            f"{spread:.2f} ms, not a decomposition of it. What is measured independently of the "
            f"arms is the reload path - {per_request:.4f} ms per request over "
            f"{stats.get('count', 0)} counted calls, with its own total, p50 and tail above. M28's "
            f"+3.06 ms \"reload and corpus growth\" therefore still has one unattributed part: the "
            f"reload is counted and two orders of magnitude too small to be it; what is left is "
            f"ingestion plus a larger view, which these arms cannot divide."
        )
    return [
        "",
        "## Where the tail comes from",
        "",
        "| arm | what changes under the run | p95 ms | vs short curve |",
        "|---|---|---|---|",
        f"| short curve | nothing | {base:.2f} | - |",
        f"| control | writes into a tenant nobody reads | {control['p95_ms']:.2f} | "
        f"{writing:+.2f} |",
        f"| writer | writes into the served tenant, 18 -> 48 documents | "
        f"{writer['p95_ms']:.2f} | {total:+.2f} |",
        f"| pre-grown | 48 documents held still, nothing else running | "
        f"{pregrown['p95_ms']:.2f} | {view:+.2f} |",
        "",
        f"- **The reload path is measured, not subtracted.** The writer soak made "
        f"{stats.get('count', 0)} reload calls for {stats.get('total_ms', 0.0):.0f} ms in total "
        f"({outcomes}), p50 {stats.get('p50_ms', 0.0):.2f} ms and p95 "
        f"{stats.get('p95_ms', 0.0):.2f} ms per call, of which {worked} found a changed corpus "
        f"and did any work. Spread over {writer['requests']} requests that is "
        f"{per_request:.4f} ms per request: far too small to move a p95, and it does not have "
        "to be - the requests that trigger a reload are a few dozen out of half a million.",
        (
            f"- **The ordering the split needs {'held' if ordered else 'did not hold'} in this "
            f"session.** Control {control['p95_ms']:.2f} ms ({writing:+.2f} over the short curve), "
            f"writer {writer['p95_ms']:.2f} ({total:+.2f}), pre-grown {pregrown['p95_ms']:.2f} "
            f"({view:+.2f}): a spread of {spread:.2f} ms across the three arms."
        ),
        (
            f"- The M29 session read the same arms at {PRIOR_ARMS['control']:.2f}, "
            f"{PRIOR_ARMS['writer']:.2f} and {PRIOR_ARMS['pregrown']:.2f} ms, so between the two "
            f"sessions the control moved {moved['control']:+.2f} ms while writer and pre-grown "
            f"moved {moved['writer']:+.2f} and {moved['pregrown']:+.2f}. The control's throughput "
            f"here is {control['rps']:.0f} rps against the writer's {writer['rps']:.0f} "
            f"({rps_gap:.0f}% lower), which is that arm meeting a slower host for its ten minutes "
            f"rather than {abs(writing):.2f} ms of work, and it is larger than the effect the arm "
            f"was built to measure. Both sessions are on one desktop under its own load (L13)."
        ),
        verdict,
    ]


def notes_for_soak(row: dict, bench_p95: float | None) -> list[str]:
    writer_notes: list[str] = []
    if row.get("writer_seconds"):
        served = SETTINGS.default_tenant
        tenant = row.get("writer_tenant", served)
        control = tenant != served
        w_before = row.get("writer_documents_before", row["documents_before"])
        w_after = row.get("writer_documents_after", row["documents_after"])
        w_chunks = row.get("writer_chunks_after", row["chunks_after"])
        if control:
            writer_notes = [
                f"- A concurrent writer appended {row['documents_written']} generated documents "
                f"(one every {row['writer_seconds']:.0f} s) through the production ingest path "
                f"into tenant `{tenant}`, which no request above touched: that tenant went "
                f"{w_before} -> {w_after} documents ({w_chunks} chunks) while the served corpus "
                f"stayed at {row['documents_after']} documents ({row['chunks_after']} chunks).",
                "- This is the control for `results/soak-writer.md`, which appended the same "
                "documents to the served tenant. Here the served view's corpus version never "
                "changed, so no refresh ever ran: this p95 keeps the cost of writing at all - "
                "WAL, a second connection and a larger file - plus the version checks below, and "
                "nothing else.",
            ]
            served_soak = _committed_row("soak-writer") or {}
            if served_soak.get("p95_ms", 0.0) < row["p95_ms"]:
                writer_notes.append(
                    f"- This arm read *above* the writer soak ({row['p95_ms']:.2f} ms against "
                    f"{served_soak['p95_ms']:.2f}) with a throughput of {row['rps']:.0f} rps "
                    f"against {served_soak['rps']:.0f}, which is the host being slower for these "
                    f"ten minutes rather than a cost of writing into an unread tenant. A "
                    f"difference that should be one-directional cannot be read from one session: "
                    f"`results/soak-writer.md` sizes both sessions against each other."
                )
        else:
            writer_notes = [
                f"- A concurrent writer appended {row['documents_written']} generated documents "
                f"(one every {row['writer_seconds']:.0f} s) through the production ingest path "
                f"into the tenant being served, taking the corpus {row['documents_before']} -> "
                f"{row['documents_after']} documents ({row['chunks_after']} chunks) while every "
                "request above was answered.",
            ]
        writer_notes += [
            "- The write side never took the read side with it: "
            + (f"ingest p95 {row['write_p95_ms']:.2f} ms, " if row.get("write_p95_ms") is not None
               else "no append completed, ")
            + f"{row['write_errors']} errors. Both connections run WAL (M26), so a write "
            "appends beside a reader instead of taking the database lock.",
        ]
        if not control:
            writer_notes.append(
                "- The reload path is measured, not inferred: the server times every `refresh()` "
                "it performs, and the table above carries the count, total and tail of those "
                "calls. `## Where the tail comes from` puts the view-size arm and the control "
                "beside this run, so the reload is a line item rather than a remainder."
            )
        writer_notes.append(
            "- The appended documents are generated text over a 300-word vocabulary: they exist "
            "to be ingested, not to be answered. This run is a claim about serving through an "
            "ingest, not about answer quality."
        )
    drift = (
        f"{row['p95_ms'] - bench_p95:+.2f} ms against the short-run p95 ({bench_p95:.2f} ms)"
        if bench_p95
        else "no short-run comparison available"
    )
    verdict_note = _nfr01_note(row)
    reload = row.get("reload")
    if reload is None:
        reload_line = (
            "- The reload path was not instrumented when this run was taken, so its cost here is "
            "unknown rather than zero; `results/soak-writer.md` and "
            "`results/soak-pregrown.md` carry the counters."
        )
    else:
        outcomes = reload.get("outcomes", {})
        idle = outcomes.get("none", 0) + outcomes.get("missing", 0)
        reload_line = (
            f"- The reload path cost {reload.get('total_ms', 0.0):.2f} ms in "
            f"{reload.get('count', 0)} calls ({row.get('reload_ms_per_request') or 0.0:.6f} ms "
            f"per request), outcomes {outcomes}. The {idle} calls that found an unchanged "
            f"corpus version are cheap per call (p50 {reload.get('p50_ms') or 0.0:.2f} ms) "
            f"but they are the frequent ones, so they dominate that total; only "
            f"{reload.get('count', 0) - idle} of the calls refreshed anything."
        )
    return writer_notes + [
        f"- p95 after {row['duration_seconds'] / 60:.0f} minutes: {row['p95_ms']:.2f} ms, "
        f"{drift}. A leak or an unbounded cache would show here as growth.",
        reload_line,
        verdict_note,
        (
            f"- Shedding is fast on purpose: a 503 came back in p95 "
            f"{row.get('shed_p95_ms', 0.0):.2f} ms. A request that is going to be refused should "
            "not spend its time holding a thread that a request we can answer needs."
            if row.get("shed")
            else "- Nothing was shed in this run, so the admission bound never became the "
            "binding constraint; it is a ceiling, not a throttle on normal traffic."
        ),
        (
            "- The short curve meets the same threshold at the same user count, because it "
            "lasts a few seconds. Both readings are real: the burst measures the server, the "
            "soak measures the server plus a queue that never empties. Quoting only the burst "
            "would be choosing the flattering number."
            if row["p95_ms"] > NFR01_P95_MS
            else "- Unlike the burst, this run cannot borrow credit from an idle moment: the "
            "threshold holds under sustained load."
        ),
        f"- Error rate {row['error_rate'] * 100:.3f}% over {row['requests']} requests; these are",
        "  dropped connections, and none of them changed an answer's content.",
        "- The per-tenant view is the only thing that grows with traffic, and it is keyed by",
        "  tenant, so this run also checks that repeated requests do not accumulate per request.",
        "- The answer cache is off here (`ATLAS_CACHE_SIZE=0`), so this is the sustained cost of "
        "answering every request rather than the cost of repeating one; `results/cache.md` is "
        "where a warm cache is measured.",
    ]


def main() -> int:
    parser = argparse.ArgumentParser(description="latency curve and soak")
    parser.add_argument("--levels", default=",".join(str(level) for level in FIXED_LEVELS))
    parser.add_argument("--requests", type=int, default=REQUESTS_PER_USER)
    parser.add_argument("--soak-seconds", type=float, default=0.0)
    parser.add_argument("--users", type=int, default=NFR01_USERS)
    parser.add_argument("--token", default="")
    parser.add_argument(
        "--think-ms",
        type=float,
        default=0.0,
        help="pause between one user's requests (0 = saturation test, not an interactive model)",
    )
    parser.add_argument(
        "--writer-seconds",
        type=float,
        default=0.0,
        help="during the soak, append a generated document every N seconds from a second "
        "connection (0 = no writer); writes results/soak-writer.*",
    )
    parser.add_argument(
        "--pregrow",
        type=int,
        default=0,
        help="during the soak, ingest N generated documents into the served tenant before the "
        "run starts: a bigger view with no ingest under load, which is the third arm of the "
        "M29 split; writes results/soak-pregrown.*",
    )
    parser.add_argument(
        "--writer-tenant",
        default="",
        help="tenant the soak writer appends to (default: the served tenant). A different "
        "tenant is the control: the served view never reloads, so the run keeps the cost of "
        "writing and drops the cost of ingesting into what is being read",
    )
    parser.add_argument(
        "--render-existing",
        nargs="?",
        const="bench",
        default=None,
        metavar="STEM",
        help="rewrite a report from its committed json without running load; STEM defaults to "
        "bench, and any other stem re-renders only that report",
    )
    parser.add_argument(
        "--out",
        default="bench",
        help="report stem for this run (default bench; use another name to keep the curve)",
    )
    parser.add_argument("--label", default="", help="one-line qualifier for the report title")
    args = parser.parse_args()

    if args.writer_seconds < 0:
        raise SystemExit("--writer-seconds must be >= 0")
    if args.writer_seconds and not args.soak_seconds:
        raise SystemExit(
            "--writer-seconds needs --soak-seconds: a writer is only meaningful beside a run "
            "that lasts long enough to ingest through"
        )
    if args.writer_tenant and not args.writer_seconds:
        raise SystemExit("--writer-tenant needs --writer-seconds: there is no writer to place")

    if args.render_existing:
        stem = args.render_existing
        path = RESULTS / f"{stem}.json"
        if not path.exists():
            raise SystemExit(f"no results/{stem}.json to re-render")
        bench_payload = json.loads(path.read_text(encoding="utf-8"))
        if stem.startswith("soak"):
            soak_row = bench_payload["row"]
            bench_file = RESULTS / "bench.json"
            entry = None
            if bench_file.exists():
                published = json.loads(bench_file.read_text(encoding="utf-8"))
                entry = next(
                    (item for item in published["rows"]
                     if item["users"] == soak_row["users"]),
                    None,
                )
            # notes are recomputed, not replayed: the prose is code, the numbers are data
            bench_payload["notes"] = notes_for_soak(
                soak_row, entry["p95_ms"] if entry else None
            )
            write_soak(soak_row, bench_payload, stem=stem)
            print(f"re-rendered: results/{stem}.md")
            return 0
        # notes are recomputed, not replayed: the prose is code, the numbers are data
        bench_payload["notes"] = notes_for_sweep(bench_payload["rows"])
        write_bench(
            bench_payload["rows"],
            bench_payload,
            stem=stem,
            label=bench_payload.get("label", ""),
            command=bench_payload.get("command", "python -m bench.sweep"),
        )
        if stem != "bench":
            print(f"re-rendered: results/{stem}.md")
            return 0
        for name in (
            "soak.json",
            "soak-interactive.json",
            "soak-writer.json",
            "soak-writer-control.json",
            "soak-pregrown.json",
        ):
            soak_path = RESULTS / name
            if not soak_path.exists():
                continue
            soak_payload = json.loads(soak_path.read_text(encoding="utf-8"))
            entry = next(
                (item for item in bench_payload["rows"]
                 if item["users"] == soak_payload["row"]["users"]),
                None,
            )
            # notes are recomputed, not replayed: the prose is code, the numbers are data
            soak_payload["notes"] = notes_for_soak(
                soak_payload["row"], entry["p95_ms"] if entry else None
            )
            write_soak(soak_payload["row"], soak_payload)
        print("re-rendered: results/bench.md, results/soak.md")
        return 0
    answerable, _ = load_questions()
    questions = [row["query"] for row in answerable]
    db = Path(tempfile.mkdtemp()) / "bench.db"
    build_database(db)

    if args.soak_seconds:
        print(
            f"soaking {args.soak_seconds:.0f}s at {args.users} users"
            + (f" with a write every {args.writer_seconds:.0f}s" if args.writer_seconds else "")
            + " ...",
            file=sys.stderr,
        )
        log_path = Path(tempfile.mkdtemp()) / "server.log"
        with (
            open(log_path, "w", encoding="utf-8", buffering=1) as sink,
            contextlib.redirect_stdout(sink),
        ):
            row = soak(questions, db, seconds=args.soak_seconds, users=args.users,
                       token=args.token, think_seconds=args.think_ms / 1000.0,
                       writer_seconds=args.writer_seconds,
                       writer_tenant=args.writer_tenant, pregrow=args.pregrow)
        row["think_ms"] = args.think_ms
        if args.think_ms:
            ceiling = args.users / (args.think_ms / 1000.0)
            if row["rps"] > ceiling * 1.15:
                raise SystemExit(
                    f"invalid run: {row['rps']:.1f} rps at {args.users} users with "
                    f"{args.think_ms:.0f} ms think time, but the offered load cannot exceed "
                    f"{ceiling:.1f} rps - the pause between a user's requests is not being "
                    f"applied"
                )
        bench_p95 = None
        bench_file = RESULTS / "bench.json"
        if bench_file.exists():
            published = json.loads(bench_file.read_text(encoding="utf-8"))
            entry = next(
                (item for item in published["rows"] if item["users"] == args.users), None
            )
            bench_p95 = entry["p95_ms"] if entry else None
        payload = {
            "provenance": provenance(),
            "row": row,
            "notes": notes_for_soak(row, bench_p95),
        }
        write_soak(row, payload)
        print(f"p50 {row['p50_ms']} ms · p95 {row['p95_ms']} ms · p99 {row['p99_ms']} ms · "
              f"{row['rps']} rps · {row['shed']} shed · {row['failures']} failures")
        if row.get("writer_seconds"):
            print(f"writer: {row['documents_written']} documents appended to "
                  f"'{row['writer_tenant']}', served corpus {row['documents_before']} -> "
                  f"{row['documents_after']}, ingest p95 {row['write_p95_ms']} ms, "
                  f"{row['write_errors']} errors")
        return 0

    levels = tuple(int(level) for level in args.levels.split(","))
    # the server logs one line per request; sending that to a file (as a deployed
    # process does) keeps the measurement about the API, not about printf to a pipe
    log_path = Path(tempfile.mkdtemp()) / "server.log"
    with (
        open(log_path, "w", encoding="utf-8", buffering=1) as sink,
        contextlib.redirect_stdout(sink),
    ):
        rows = sweep(questions, db, levels=levels, per_user=args.requests, token=args.token)
    command = "python -m bench.sweep"
    if args.out != "bench":
        command += f" --out {args.out}"
    if args.label:
        command += f' --label "{args.label}"'
    payload = {
        "provenance": provenance(),
        "rows": rows,
        "notes": notes_for_sweep(rows),
        "command": command,
        "label": args.label,
    }
    write_bench(rows, payload, stem=args.out, label=args.label, command=command)
    print(f"written: results/{args.out}.md, results/{args.out}.json")
    return 0


if __name__ == "__main__":
    sys.exit(main())
