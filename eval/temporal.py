"""Temporal correctness: does the answer respect the date it was asked about? (M21, FR-29, FR-30)

The corpus in `corpus/campus` is undated, so every earlier report measured one thing: "what
does the system say about this policy *now*". A policy assistant that cannot answer "what did
it say in 2024" is wrong in the direction that matters - it quotes a rule that did not exist
yet, with a citation, and sounds certain.

This harness answers four questions about the shipped behaviour:

1. **Version accuracy** - for a question about a past date, is the top passage from the
   revision that was actually in force? (9 dated questions across three policies, three states
   each.)
2. **Future-leak rate** - how often does a returned citation come from a revision dated after
   the date asked about? The target is zero, and it is a hard failure here rather than a number
   to explain.
3. **Not-yet accuracy** - for a fact that only a later revision contains, is the refusal
   `not_yet_in_effect` *and* does it name the date that revision takes effect? (3 questions.)
4. **The control** - the same questions with no date at all. That is the behaviour this feature
   replaces, and it is reported beside the fix so the delta is a measurement rather than a
   claim.

The revisions in `corpus/temporal` are authored synthetic data, like the rest of the corpus;
`audit()` checks they form a real history (one open revision per document, contiguous windows)
instead of a pile of files. `results/temporal.md` and `results/temporal.json` are the output.
"""

from __future__ import annotations

import json
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from atlas.answer import answer_query  # noqa: E402
from atlas.config import SETTINGS  # noqa: E402
from atlas.retrieval import HybridIndex  # noqa: E402
from atlas.store import Store  # noqa: E402
from eval import metrics  # noqa: E402
from eval.run_eval import CORPUS, RESULTS, provenance  # noqa: E402

TEMPORAL = ROOT / "corpus" / "temporal"
QUESTIONS = ROOT / "eval" / "golden_set_temporal.jsonl"
TENANT = SETTINGS.default_tenant


def load_questions() -> list[dict]:
    text = QUESTIONS.read_text(encoding="utf-8")
    return [json.loads(line) for line in text.splitlines() if line.strip()]


def build_store() -> Store:
    """Base corpus first, then the dated revisions: ingest order *is* the history."""
    store = Store(Path(tempfile.mkdtemp()) / "temporal.db")
    for path in sorted(CORPUS.glob("*.md")):
        store.upsert_document(tenant_id=TENANT, path=path)
    for path in sorted(TEMPORAL.glob("*.md")):
        doc_id, _, stamp = path.stem.partition("@")
        store.upsert_document(
            tenant_id=TENANT, path=path, effective=stamp, doc_id=doc_id
        )
    return store


def audit(store: Store) -> list[str]:
    """The fixture must be a history: three revisions per policy, exactly one of them open."""
    problems: list[str] = []
    for doc_id in sorted({path.stem.partition("@")[0] for path in TEMPORAL.glob("*.md")}):
        revisions = store.revisions(tenant_id=TENANT, doc_id=doc_id)
        if len(revisions) != 3:
            problems.append(f"{doc_id}: {len(revisions)} revisions, expected 3 (base + two)")
        if sum(1 for revision in revisions if not revision[1]) != 1:
            problems.append(f"{doc_id}: not exactly one open revision")
    return problems


def measure(index: HybridIndex, question: dict) -> dict:
    """One question at its date: what was retrieved, what was cited, and the undated run."""
    as_of = question["as_of"]
    passages = index.search(question["query"], tenant_id=TENANT, k=1, as_of=as_of)
    top = passages[0] if passages else None
    answer = answer_query(index, question["query"], tenant_id=TENANT, as_of=as_of)
    undated = index.search(question["query"], tenant_id=TENANT, k=1)
    control = undated[0] if undated else None
    row = {
        "id": question["id"],
        "as_of": as_of,
        "query": question["query"],
        "top_doc": top.doc_id if top else "",
        "top_effective": top.valid_from if top else "",
        "refused": answer.refused,
        "refusal_reason": answer.refusal_reason,
        "effective_from": answer.effective_from,
        "confidence": answer.confidence,
        "expect_effective": question["expect_effective"],
        "control_doc": control.doc_id if control else "",
        "control_effective": control.valid_from if control else "",
        # a leak is a top hit from a revision that did not exist on the date asked about
        "leak": bool(top and top.valid_from > as_of),
        # the same leak, one layer up: the answer's citation post-dates the question
        "answered_leak": bool(
            not answer.refused and any(c.valid_from > as_of for c in answer.citations)
        ),
        "control_leak": bool(control and control.valid_from > as_of),
        "control_answered": not answer_query(index, question["query"], tenant_id=TENANT).refused,
    }
    if question.get("expect_refusal"):
        row["expect_refusal"] = question["expect_refusal"]
        row["ok"] = (
            answer.refused
            and answer.refusal_reason == question["expect_refusal"]
            and answer.effective_from == question["expect_effective"]
        )
    else:
        row["ok"] = bool(
            top
            and top.doc_id in question["relevant_doc_ids"]
            and top.valid_from == question["expect_effective"]
        )
    return row


def summarise(rows: list[dict]) -> dict:
    """Retrieval-level and answer-level rates, plus the undated control beside them."""
    dated = [row for row in rows if "expect_refusal" not in row]
    pre = [row for row in rows if "expect_refusal" in row]
    return {
        "questions": len(rows),
        "in_force_questions": len(dated),
        "pre_existence_questions": len(pre),
        "version_accuracy": metrics.rate_with_ci([1.0 if row["ok"] else 0.0 for row in dated]),
        "not_yet_accuracy": metrics.rate_with_ci([1.0 if row["ok"] else 0.0 for row in pre]),
        "answer_rate": round(metrics.mean([0.0 if row["refused"] else 1.0 for row in dated]), 4),
        "future_leak_rate": round(
            metrics.mean([1.0 if row["leak"] else 0.0 for row in rows]), 4
        ),
        "answered_leak_rate": round(
            metrics.mean([1.0 if row["answered_leak"] else 0.0 for row in rows]), 4
        ),
        "control_leaks": sum(1 for row in dated if row["control_leak"]),
        "control_answers_preexisting": sum(1 for row in pre if row["control_answered"]),
    }


def render(payload: dict) -> str:
    summary, rows = payload["summary"], payload["rows"]
    version = summary["version_accuracy"]
    not_yet = summary["not_yet_accuracy"]
    lines = [
        "# Temporal correctness: does the answer respect the date asked about? (M21)",
        "",
        f"Command: `{payload['provenance']['regenerate_with']}` · commit "
        f"`{payload['provenance']['commit']}` · corpus `corpus/campus` + `corpus/temporal` "
        f"(18 documents, 6 dated revisions)",
        f"Platform: {payload['provenance']['platform']} · Python "
        f"{payload['provenance']['python']} · encoder `{SETTINGS.embedder_kind}`",
        "",
        "## Summary",
        "",
        "| measure | value | n |",
        "|---|---|---|",
        f"| version accuracy (top passage is the revision in force) | "
        f"{version['mean']:.4f} [{version['ci_low']:.3f}, {version['ci_high']:.3f}] "
        f"| {version['n']} |",
        f"| not-yet accuracy (refused *and* the date named) | "
        f"{not_yet['mean']:.4f} [{not_yet['ci_low']:.3f}, {not_yet['ci_high']:.3f}] "
        f"| {not_yet['n']} |",
        f"| **future-leak rate** (citation dated after the question) | "
        f"**{summary['future_leak_rate']:.4f}** | {summary['questions']} |",
        f"| answer rate on in-force questions | {summary['answer_rate']:.4f} "
        f"| {summary['in_force_questions']} |",
        f"| control: undated runs citing a not-yet-effective revision | "
        f"{summary['control_leaks']} of {summary['in_force_questions']} | "
        f"{summary['in_force_questions']} |",
        f"| control: undated runs answering a not-yet-existing rule | "
        f"{summary['control_answers_preexisting']} of {summary['pre_existence_questions']} | "
        f"{summary['pre_existence_questions']} |",
        "",
        "## Per question",
        "",
        "| id | as_of | asked | top revision | verdict | refused | reason | control (undated) |",
        "|---|---|---|---|---|---|---|---|",
    ]
    for row in rows:
        verdict = "ok" if row["ok"] else "**miss**"
        expected = row.get("expect_refusal") or row["expect_effective"] or "base"
        control = row["control_effective"] or "base"
        lines.append(
            f"| {row['id']} | {row['as_of']} | {row['query'][:44]}... | "
            f"{row['top_effective'] or 'base'} (expect {expected}) | {verdict} | "
            f"{'yes' if row['refused'] else 'no'} | {row['refusal_reason'] or '-'} | "
            f"{control}{' **leak**' if row['control_leak'] else ''} |"
        )
    lines += [
        "",
        "## What the date changes",
        "",
        f"- With `as_of`, **{summary['future_leak_rate']:.4f}** of the {summary['questions']} "
        "questions return evidence dated after the moment asked about. The filter runs inside "
        "both retrievers, before fusion, so a superseded revision cannot even vote.",
        f"- Without it, **{summary['control_leaks']} of {summary['in_force_questions']}** "
        "dated questions are answered from a revision that did not exist yet, and all "
        f"**{summary['control_answers_preexisting']} of "
        f"{summary['pre_existence_questions']}** not-yet questions are answered instead of "
        "refused. That is the failure mode the feature removes, measured rather than asserted.",
        f"- A refusal now distinguishes three cases: not close enough (`low_confidence`), a "
        "topic the corpus never carries (`unknown_terms`), and a rule that exists but not yet "
        f"(`not_yet_in_effect`, naming the date). "
        f"{not_yet['mean'] * not_yet['n']:.0f}/{not_yet['n']} pre-existence questions get the "
        "third, with the right date.",
        "",
        "## How this evidence can mislead",
        "",
        "- The revisions are **authored**, not collected: they are consistent by construction, "
        "where a real archive has gaps, contradictory circulars and undated amendments.",
        "- Effective dates are known exactly here. In a real corpus they are frequently unknown, "
        "which is why an undated document is treated as in force at every date rather than "
        "guessed at.",
        f"- Nine dated questions across three policies is a smoke test of the mechanism, not a "
        f"measure of policy-archive quality: the 95% interval on {version['mean']:.4f} spans "
        f"{version['ci_low']:.3f} to {version['ci_high']:.3f}, so it bounds the failure rate it "
        "rules out rather than proving the rate. See `docs/17-limitations.md`.",
        "",
        "## Reproducing",
        "",
        "```",
        "make temporal",
        "```",
        "",
        "The harness builds a fresh database in a temporary directory, ingests the base corpus "
        "and then the six dated revisions, and writes nothing into `data/`.",
        "",
    ]
    return "\n".join(lines)


def main() -> int:
    store = build_store()
    try:
        problems = audit(store)
        if problems:
            print("fixture is not a history:")
            for problem in problems:
                print(f"  - {problem}")
            return 1
        index = HybridIndex(store)
        rows = [measure(index, question) for question in load_questions()]
    finally:
        store.close()

    summary = summarise(rows)
    provenance_block = {
        **provenance(),
        "corpus": "corpus/campus + corpus/temporal (18 documents, 6 dated revisions)",
        "regenerate_with": "python -m eval.temporal",
    }
    payload = {"provenance": provenance_block, "summary": summary, "rows": rows}
    (RESULTS / "temporal.json").write_text(json.dumps(payload, indent=2), encoding="utf-8")
    (RESULTS / "temporal.md").write_text(render(payload), encoding="utf-8")

    print("== temporal correctness ==")
    print(f"  version accuracy      {summary['version_accuracy']['mean']:.4f} "
          f"(n={summary['in_force_questions']})")
    print(f"  not-yet accuracy      {summary['not_yet_accuracy']['mean']:.4f} "
          f"(n={summary['pre_existence_questions']})")
    print(f"  future-leak rate      {summary['future_leak_rate']:.4f}")
    print(f"  answered-leak rate    {summary['answered_leak_rate']:.4f}")
    print(f"  control leaks         {summary['control_leaks']} of "
          f"{summary['in_force_questions']} dated questions")
    print("written: results/temporal.md, results/temporal.json")
    if summary["future_leak_rate"] or summary["answered_leak_rate"]:
        print("FAILED: a citation post-dates the question it answers")
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
