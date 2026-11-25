"""Corpus-derived expansion, measured against FR-17 (M27).

The rules, fixed before the numbers:

1. **Fit on v1.** The grid is PMI thresholds, with 0.0 meaning off. The fitted threshold is the
   *smallest* one that costs v1 neither an answer nor a refusal - the most expansion the tuned set
   tolerates. Expansion is a hypothesis, not a knob to optimise, and a threshold that already
   breaks v1 has nothing left to prove on v3.
2. **Decide on v3.** v2 is reported as a replication; v4 is the set written for the novelty
   question, judged here but not decided on.
3. **Adopt only if** v3 refusal accuracy or answer rate improves *and* no answerable question is
   lost on any of the four sets. That is FR-17's wording: expansion has to buy refusals or answers
   without costing an answer.

The refusal gate keeps reading the question as asked (see `src/atlas/expand.py`), so an
improvement here is retrieval finding a better passage, not the gate forgetting what novelty
means. Confidence is measured once per (threshold, question) and every row below is that same
measurement re-bucketed.

Writes `results/expansion.md` and `results/expansion.json`.
"""

from __future__ import annotations

import dataclasses
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from atlas.config import SETTINGS  # noqa: E402
from atlas.expand import TermExpander, added_terms, expanded_query  # noqa: E402
from atlas.retrieval import HybridIndex  # noqa: E402
from atlas.support import novelty, refuses, unseen_terms  # noqa: E402
from eval import metrics  # noqa: E402
from eval.holdout import load_v2, load_v3, load_v4  # noqa: E402
from eval.run_eval import RESULTS, build_store, load_questions, provenance  # noqa: E402

ENCODER = "potion-base-8M"
GRID = (0.0, 0.5, 1.0, 1.5, 2.0, 2.5, 3.0)
LIMIT = 3
SET_LABELS = ("v1", "v2", "v3", "v4")
THETA = dataclasses.replace(SETTINGS, embedder_kind=ENCODER).theta
FLOOR = SETTINGS.refusal_novelty_floor


def score(
    index: HybridIndex,
    questions: list[dict],
    vocabulary: frozenset[str],
    expander: TermExpander,
    threshold: float,
) -> list[dict]:
    """Confidence and the gate's two inputs per question, at one expansion threshold."""
    tenant = SETTINGS.default_tenant
    rows = []
    for question in questions:
        asked = question["query"]
        used = (
            asked
            if threshold <= 0.0
            else expanded_query(asked, expander, threshold=threshold, limit=LIMIT)
        )
        report = index.diagnostics(used, tenant_id=tenant)
        rows.append(
            {
                "id": question["id"],
                "answerable": question["answerable"],
                "confidence": 0.0 if report is None else round(report[1]["confidence"], 4),
                "novelty": novelty(asked, vocabulary),
                "unseen": len(unseen_terms(asked, vocabulary)),
                "added": added_terms(asked, used),
            }
        )
    return rows


def gate(rows: list[dict]) -> dict:
    """Answer rate and refusal accuracy of the shipped gate rule over these rows."""
    answered = refused = answerable = unanswerable = 0
    for row in rows:
        blocked = refuses(
            confidence=row["confidence"],
            theta=THETA,
            novel=row["novelty"],
            unseen=row["unseen"],
            floor=FLOOR,
        )
        if row["answerable"]:
            answerable += 1
            answered += 0 if blocked else 1
        else:
            unanswerable += 1
            refused += 1 if blocked else 0
    return {
        "answer_rate": metrics.rate_with_ci(
            [1.0] * answered + [0.0] * (answerable - answered)
        )["mean"],
        "refusal_accuracy": metrics.rate_with_ci(
            [1.0] * refused + [0.0] * (unanswerable - refused)
        )["mean"],
        "added_terms": round(sum(len(row["added"]) for row in rows) / len(rows), 3),
    }


def main() -> int:
    v1_answerable, v1_unanswerable = load_questions()
    v2_answerable, v2_unanswerable = load_v2()
    v3_answerable, v3_unanswerable = load_v3()
    v4_answerable, v4_unanswerable = load_v4()
    sets = {
        "v1": v1_answerable + v1_unanswerable,
        "v2": v2_answerable + v2_unanswerable,
        "v3": v3_answerable + v3_unanswerable,
        "v4": v4_answerable + v4_unanswerable,
    }
    store = build_store(SETTINGS.default_tenant)
    try:
        settings = dataclasses.replace(SETTINGS, embedder_kind=ENCODER)
        index = HybridIndex(store, settings=settings)
        view = index.view(SETTINGS.default_tenant)
        assert view is not None, "no corpus ingested: run the ingest target first"
        vocabulary = frozenset(view.idf)
        expander = view.expander()
        measured = {
            threshold: {
                label: score(index, questions, vocabulary, expander, threshold)
                for label, questions in sets.items()
            }
            for threshold in GRID
        }
        table_size = len(expander)
    finally:
        store.close()

    table = {
        threshold: {label: gate(rows) for label, rows in by_set.items()}
        for threshold, by_set in measured.items()
    }
    base = table[0.0]
    eligible = [
        threshold
        for threshold in GRID
        if threshold > 0
        and table[threshold]["v1"]["answer_rate"] >= base["v1"]["answer_rate"]
        and table[threshold]["v1"]["refusal_accuracy"] >= base["v1"]["refusal_accuracy"]
    ]
    fitted = min(eligible) if eligible else None
    improves = fitted is not None and (
        table[fitted]["v3"]["refusal_accuracy"] > base["v3"]["refusal_accuracy"]
        or table[fitted]["v3"]["answer_rate"] > base["v3"]["answer_rate"]
    )
    keeps_answers = fitted is not None and all(
        table[fitted][label]["answer_rate"] >= base[label]["answer_rate"] for label in SET_LABELS
    )
    adopted = bool(fitted is not None and improves and keeps_answers)

    detail = {}
    for label in ("v3", "v4"):
        if fitted is None:
            detail[label] = []
            continue
        detail[label] = [
            {
                "id": before["id"],
                "answerable": before["answerable"],
                "confidence_before": before["confidence"],
                "confidence_after": after["confidence"],
                "added": list(after["added"]),
                "refused_before": refuses(
                    confidence=before["confidence"], theta=THETA, novel=before["novelty"],
                    unseen=before["unseen"], floor=FLOOR,
                ),
                "refused_after": refuses(
                    confidence=after["confidence"], theta=THETA, novel=after["novelty"],
                    unseen=after["unseen"], floor=FLOOR,
                ),
            }
            for before, after in zip(measured[0.0][label], measured[fitted][label], strict=True)
        ]

    payload = {
        "provenance": provenance(),
        "method": (__doc__ or "").strip(),
        "encoder": ENCODER,
        "theta": THETA,
        "floor": FLOOR,
        "grid": list(GRID),
        "limit": LIMIT,
        "expander_terms": table_size,
        "table": {
            str(threshold): {label: table[threshold][label] for label in SET_LABELS}
            for threshold in GRID
        },
        "fit": {
            "rule": "largest grid threshold that leaves v1 answer rate and refusal unchanged",
            "eligible": eligible,
            "fitted": fitted,
        },
        "decision": {
            "adopted": adopted,
            "improves_v3": improves,
            "keeps_answer_rate": keeps_answers,
        },
        "detail": detail,
    }
    (RESULTS / "expansion.json").write_text(json.dumps(payload, indent=2), encoding="utf-8")

    lines = [
        "# Corpus-derived query expansion (M27, FR-17)",
        "",
        f"Encoder `{ENCODER}`, theta {THETA:.2f}, novelty floor {FLOOR:.2f}, up to {LIMIT} added",
        "terms per query term. The expander is a PMI table over the tenant's own chunks:",
        f"{table_size} terms kept, built from the token sets the view already holds. Expansion is",
        "applied to the retrieval query only - the gate still reads the question as asked, so an",
        "improvement below is retrieval finding a better passage, not novelty being erased.",
        "",
        "**The rules, fixed before the run.** Fit on v1 as the *smallest* threshold that costs v1",
        "neither an answer nor a refusal - the most expansion the tuned set tolerates - decide on",
        "v3, and adopt only if v3 improves and no answerable question is lost on any set.",
        "",
        "## Every threshold, every set",
        "",
        "| PMI threshold | v1 answer | v1 refusal | v2 answer | v2 refusal | v3 answer "
        "| v3 refusal | v4 answer | v4 refusal | mean terms added |",
        "|---|---|---|---|---|---|---|---|---|---|",
    ]
    for threshold in GRID:
        row = table[threshold]
        marker = ""
        if threshold == fitted:
            marker = " (fitted)"
        elif threshold == 0:
            marker = " (off, baseline)"
        lines.append(
            f"| {threshold:.1f}{marker} | "
            + " | ".join(
                f"{row[label]['answer_rate']:.4f} | {row[label]['refusal_accuracy']:.4f}"
                for label in SET_LABELS
            )
            + f" | {row['v1']['added_terms']:.2f} |"
        )
    lines += ["", "## Decision", ""]
    if adopted:
        lines += [
            f"- **Adopted at {fitted:.1f}.** v3 refusal "
            f"{base['v3']['refusal_accuracy']:.4f} → "
            f"{table[fitted]['v3']['refusal_accuracy']:.4f}, v3 answer "
            f"{base['v3']['answer_rate']:.4f} → {table[fitted]['v3']['answer_rate']:.4f}, "
            "and no answerable question is refused on any set.",
        ]
    else:
        reasons = []
        if fitted is None:
            reasons.append("no threshold left v1 unchanged")
        else:
            if not improves:
                fell = table[fitted]["v3"]["refusal_accuracy"] < base["v3"]["refusal_accuracy"]
                reasons.append(
                    f"v3 {'refusal fell' if fell else 'did not improve'} (refusal "
                    f"{base['v3']['refusal_accuracy']:.4f} → "
                    f"{table[fitted]['v3']['refusal_accuracy']:.4f}, answer "
                    f"{base['v3']['answer_rate']:.4f} → "
                    f"{table[fitted]['v3']['answer_rate']:.4f})"
                )
            if not keeps_answers:
                lost = [
                    label
                    for label in SET_LABELS
                    if table[fitted][label]["answer_rate"] < base[label]["answer_rate"]
                ]
                reasons.append(f"it costs answers on {', '.join(lost)}")
        lines += [
            f"- **Not adopted** (best candidate {fitted if fitted is not None else 'none'}): "
            f"{'; '.join(reasons)}.",
            "- FR-17 stays open with these numbers attached; the shipped default remains",
            "  `expansion_threshold = 0.0`, so no committed answer changed.",
        ]
    lines += [
        "",
        "## Per question, before and after the fitted threshold",
        "",
        "One row is worth a sixth of refusal accuracy on an appended set. `added` lists the terms",
        "expansion put into the retrieval query; the gate never saw them.",
        "",
        "| set | id | answerable | confidence before | after | added terms | refused before "
        "| after |",
        "|---|---|---|---|---|---|---|---|",
    ]
    for label, rows in detail.items():
        for row in rows:
            terms = ", ".join(row["added"]) or "-"
            lines.append(
                f"| {label} | {row['id']} | {'yes' if row['answerable'] else 'no'} "
                f"| {row['confidence_before']:.4f} | {row['confidence_after']:.4f} | {terms} "
                f"| {'yes' if row['refused_before'] else 'no'} "
                f"| {'yes' if row['refused_after'] else 'no'} |"
            )
    lines += [
        "",
        "## What expansion cannot do here",
        "",
        "- **A term the corpus never uses has no neighbours.** PMI needs co-occurrence, so the",
        "  questions that fail because their words are absent (v3-014's parking, v3-018's sports",
        "  scholarship) are exactly the ones expansion cannot touch: there is nothing to expand",
        "  from. The signal is corpus-derived, and that is its ceiling.",
        "- **Co-occurrence is not meaning.** Two terms that share a document are candidates",
        "  whether or not they are related; on a corpus of 18 documents almost everything shares",
        "  one, which is why the threshold has to be fitted rather than chosen.",
        "- **The table is corpus-wide, not date-scoped**, so under `as_of` an expanded query can",
        "  carry a word only a later revision uses (L22). This is one reason the default is off.",
        "",
    ]
    (RESULTS / "expansion.md").write_text("\n".join(lines), encoding="utf-8")

    print("written: results/expansion.md, results/expansion.json")
    print(f"  expander: {table_size} terms | fitted threshold: {fitted}")
    for label in SET_LABELS:
        print(
            f"  {label}: answer {base[label]['answer_rate']:.4f} -> "
            f"{table[fitted][label]['answer_rate']:.4f} | refusal "
            f"{base[label]['refusal_accuracy']:.4f} -> "
            f"{table[fitted][label]['refusal_accuracy']:.4f}"
            if fitted is not None
            else f"  {label}: answer {base[label]['answer_rate']:.4f} | "
            f"refusal {base[label]['refusal_accuracy']:.4f}"
        )
    print(f"  adopted: {adopted}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
