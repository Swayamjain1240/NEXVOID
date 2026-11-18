"""Phrase-level novelty, measured on a fourth frozen set (M17, FR-17).

One question, asked before the numbers were produced: does pairing the question's content words
catch the refusals the single-word signal misses, without refusing questions the corpus *can*
answer?

`results/support.md` names the two survivors it cannot reach: v3-018 uses only attested words
("sports", "scholarship", "attendance") and scores 0.000 novelty, and v3-014 dilutes three unknown
words with four known ones. The first is the target here; the second is a counting problem the
report keeps visible rather than hiding.

The rules, written down before the run:

1. **A fresh set decides.** v1–v3 have all been read by earlier phases, so a new signal judged on
   them would be judged on questions it was invented after. `eval/golden_set_v4.jsonl` appends 12
   answerable and 6 unanswerable questions written for this run, closing the file at 30 questions
   across four sets.
2. **Fit on v1.** The only tuned quantity is the phrase floor: the smallest grid value strictly
   above the highest phrase novelty observed among v1's answerable questions. theta and the M14
   word floor do not move.
3. **Decide on v4.** Adopt only if v4 refusal accuracy improves over the shipped gate, and the
   answer rate does not fall on v1, v2, v3 or v4. v2 and v3 are reported as replications, not as
   evidence the decision is read from.

Writes `results/phrase.md` and `results/phrase.json`.
"""

from __future__ import annotations

import dataclasses
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT))

from atlas.config import SETTINGS  # noqa: E402
from atlas.phrase import MIN_UNSEEN_PHRASES, unseen_phrases  # noqa: E402
from atlas.phrase import novelty as phrase_novelty  # noqa: E402
from atlas.retrieval import HybridIndex  # noqa: E402
from atlas.support import MIN_UNSEEN, unseen_terms  # noqa: E402
from atlas.support import novelty as word_novelty  # noqa: E402
from atlas.text.tokenize import tokenize  # noqa: E402
from eval.holdout import V3_PATH, load_set, load_v2  # noqa: E402
from eval.run_eval import RESULTS, build_store, load_questions, provenance  # noqa: E402

ENCODER = "potion-base-8M"
V4_PATH = ROOT / "eval" / "golden_set_v4.jsonl"
FLOORS = (0.33, 0.40, 0.50, 0.60, 0.67, 0.75, 1.0)
THETA = dataclasses.replace(SETTINGS, embedder_kind=ENCODER).theta
WORD_FLOOR = SETTINGS.refusal_novelty_floor


def corpus_phrases(store, tenant: str) -> frozenset[str]:
    """Every adjacent content-word pair in the corpus, from retrieval's own tokens."""
    phrases: set[str] = set()
    for chunk in store.list_chunks(tenant_id=tenant):
        ordered = tokenize(chunk.text)
        phrases.update(f"{left} {right}" for left, right in zip(ordered, ordered[1:], strict=False))
    return frozenset(phrases)


def score(
    index, questions: list[dict], words: frozenset[str], phrases: frozenset[str]
) -> list[dict]:
    """Confidence plus both novelty signals, measured once per question."""
    tenant = SETTINGS.default_tenant
    rows = []
    for question in questions:
        report = index.diagnostics(question["query"], tenant_id=tenant)
        rows.append(
            {
                "id": question["id"],
                "answerable": question["answerable"],
                "confidence": 0.0 if report is None else round(report[1]["confidence"], 4),
                "word_novelty": word_novelty(question["query"], words),
                "word_unseen": unseen_terms(question["query"], words),
                "phrase_novelty": phrase_novelty(question["query"], phrases),
                "phrase_unseen": unseen_phrases(question["query"], phrases),
            }
        )
    return rows


def shipped(row: dict) -> bool:
    """The gate as it ships after M14: confidence, or the word-level novelty rule."""
    if row["confidence"] < THETA:
        return True
    return (
        WORD_FLOOR > 0.0
        and len(row["word_unseen"]) >= MIN_UNSEEN
        and row["word_novelty"] >= WORD_FLOOR
    )


def with_phrase_floor(row: dict, floor: float) -> bool:
    """The shipped gate plus the phrase rule - the rule only ever adds a refusal."""
    if shipped(row):
        return True
    return (
        floor > 0.0
        and len(row["phrase_unseen"]) >= MIN_UNSEEN_PHRASES
        and row["phrase_novelty"] >= floor
    )


def rates(rows: list[dict], decide) -> tuple[float, float]:
    """Answer rate over answerable rows and refusal accuracy over unanswerable ones."""
    answerable = [row for row in rows if row["answerable"]]
    unanswerable = [row for row in rows if not row["answerable"]]
    answered = sum(1 for row in answerable if not decide(row))
    refused = sum(1 for row in unanswerable if decide(row))
    return (
        round(answered / len(answerable), 4) if answerable else 0.0,
        round(refused / len(unanswerable), 4) if unanswerable else 0.0,
    )


def spread(values: list[float]) -> dict:
    return {
        "min": min(values),
        "max": max(values),
        "mean": round(sum(values) / len(values), 4),
    }


def main() -> int:
    v1_answerable, v1_unanswerable = load_questions()
    v2_answerable, v2_unanswerable = load_v2()
    v3_answerable, v3_unanswerable = load_set(V3_PATH, "v3")
    v4_answerable, v4_unanswerable = load_set(V4_PATH, "v4")
    tenant = SETTINGS.default_tenant
    store = build_store(tenant)
    try:
        settings = dataclasses.replace(SETTINGS, embedder_kind=ENCODER)
        index = HybridIndex(store, settings=settings)
        view = index.view(tenant)
        assert view is not None, "no corpus ingested: run the ingest target first"
        words = frozenset(view.idf)
        phrases = corpus_phrases(store, tenant)
        measured = {
            "v1": score(index, v1_answerable + v1_unanswerable, words, phrases),
            "v2": score(index, v2_answerable + v2_unanswerable, words, phrases),
            "v3": score(index, v3_answerable + v3_unanswerable, words, phrases),
            "v4": score(index, v4_answerable + v4_unanswerable, words, phrases),
        }
    finally:
        store.close()

    baseline = {label: rates(rows, shipped) for label, rows in measured.items()}
    worst_v1 = max(row["phrase_novelty"] for row in measured["v1"] if row["answerable"])
    eligible = [floor for floor in FLOORS if floor > worst_v1]
    floor = min(eligible) if eligible else 1.0

    grid = [
        {
            "floor": candidate,
            **{label: rates(rows, lambda row, f=candidate: with_phrase_floor(row, f))
               for label, rows in measured.items()},
        }
        for candidate in FLOORS
    ]
    adopted_rows = {
        label: rates(rows, lambda row: with_phrase_floor(row, floor))
        for label, rows in measured.items()
    }

    improves = adopted_rows["v4"][1] > baseline["v4"][1]
    keeps_answers = all(adopted_rows[label][0] >= baseline[label][0] for label in measured)
    adopted = improves and keeps_answers

    payload = {
        "provenance": provenance(),
        "method": (__doc__ or "").strip(),
        "encoder": ENCODER,
        "theta": THETA,
        "word_floor": WORD_FLOOR,
        "corpus_phrases": len(phrases),
        "fitted_floor": floor,
        "fit": {
            "rule": "smallest grid floor strictly above the highest phrase novelty among v1 "
            "answerables",
            "worst_v1_answerable_phrase_novelty": worst_v1,
            "eligible_floors": eligible,
        },
        "baseline": {label: {"answer_rate": pair[0], "refusal_accuracy": pair[1]}
                     for label, pair in baseline.items()},
        "adopted": {label: {"answer_rate": pair[0], "refusal_accuracy": pair[1]}
                    for label, pair in adopted_rows.items()},
        "grid": [
            {"floor": row["floor"],
             **{label: {"answer_rate": row[label][0], "refusal_accuracy": row[label][1]}
                for label in measured}}
            for row in grid
        ],
        "decision": {
            "adopted": adopted,
            "improves_v4_refusal": improves,
            "keeps_answer_rate": keeps_answers,
        },
        "phrase_novelty": {
            label: {
                "answerable": spread([row["phrase_novelty"] for row in rows if row["answerable"]]),
                "unanswerable": spread(
                    [row["phrase_novelty"] for row in rows if not row["answerable"]]
                ),
            }
            for label, rows in measured.items()
        },
        "detailed": {
            label: [
                {
                    "id": row["id"],
                    "answerable": row["answerable"],
                    "confidence": row["confidence"],
                    "phrase_novelty": row["phrase_novelty"],
                    "unseen": list(row["phrase_unseen"]),
                    "refused_before": shipped(row),
                    "refused_after": with_phrase_floor(row, floor),
                }
                for row in rows
            ]
            for label, rows in measured.items()
        },
    }
    RESULTS.mkdir(exist_ok=True)
    (RESULTS / "phrase.json").write_text(json.dumps(payload, indent=2), encoding="utf-8")

    lines = [
        "# Phrase-level novelty — does the corpus contain these word pairs? (M17)",
        "",
        f"Command: `python -m eval.phrase` · encoder `{ENCODER}` · theta {THETA:.2f} (unmoved) · "
        f"word floor {WORD_FLOOR:.2f} (unmoved)",
        f"Corpus `{payload['provenance']['corpus']}` · commit "
        f"`{payload['provenance']['commit']}` · {len(phrases)} distinct word pairs",
        "",
        "The shipped gate is confidence below theta **or** the M14 word rule. One rule is added",
        f"here: refuse when at least {MIN_UNSEEN_PHRASES} of the question's content pairs occur",
        "nowhere in the corpus and at least the floor's share of its pairs are unseen.",
        "Every number comes from one measurement per question, re-bucketed by floor.",
        "",
        "## The new set, before the rule",
        "",
        "v4 is the load-bearing set: 12 answerable and 6 unanswerable questions written after M16,",
        "so neither signal was designed against them.",
        "",
        "| set | answerable | unanswerable | role |",
        "|---|---|---|---|",
    ]
    roles = {"v1": "fit the floor", "v2": "replication", "v3": "replication", "v4": "decides"}
    for label in ("v1", "v2", "v3", "v4"):
        n_a = sum(1 for row in measured[label] if row["answerable"])
        n_u = sum(1 for row in measured[label] if not row["answerable"])
        lines.append(f"| {label} | {n_a} | {n_u} | {roles[label]} |")
    lines += [
        "",
        "| set | class | phrase novelty min | max | mean |",
        "|---|---|---|---|---|",
    ]
    for label, groups in payload["phrase_novelty"].items():
        for group in ("answerable", "unanswerable"):
            stats = groups[group]
            lines.append(
                f"| {label} | {group} | {stats['min']:.2f} | {stats['max']:.2f} "
                f"| {stats['mean']:.4f} |"
            )
    lines += [
        "",
        "## Before and after the fitted floor",
        "",
        f"Fitted floor **{floor:.2f}** — {payload['fit']['rule']};",
        f"the worst v1 answerable question has phrase novelty {worst_v1:.4f}.",
        "",
        "| set | baseline answer | baseline refusal | + phrase answer | + phrase refusal |",
        "|---|---|---|---|---|",
    ]
    for label in ("v1", "v2", "v3", "v4"):
        before, after = payload["baseline"][label], payload["adopted"][label]
        lines.append(
            f"| {label} | {before['answer_rate']:.4f} | {before['refusal_accuracy']:.4f} "
            f"| {after['answer_rate']:.4f} | {after['refusal_accuracy']:.4f} |"
        )
    lines += ["", "## Every floor, every set", "",
              "| floor | v1 answer | v1 refusal | v2 answer | v2 refusal | v3 answer "
              "| v3 refusal | v4 answer | v4 refusal |",
              "|---|---|---|---|---|---|---|---|---|"]
    for row in payload["grid"]:
        marker = " (fitted)" if row["floor"] == floor else ""
        cells = " | ".join(
            f"{row[label]['answer_rate']:.4f} | {row[label]['refusal_accuracy']:.4f}"
            for label in ("v1", "v2", "v3", "v4")
        )
        lines.append(f"| {row['floor']:.2f}{marker} | {cells} |")
    lines += ["", "## Decision", ""]
    if adopted:
        lines.append(
            f"- **Adopted** at floor {floor:.2f}: v4 refusal "
            f"{payload['baseline']['v4']['refusal_accuracy']:.4f} → "
            f"{payload['adopted']['v4']['refusal_accuracy']:.4f} with the v4 answer rate at "
            f"{payload['adopted']['v4']['answer_rate']:.4f}, and no set's answer rate falling."
        )
    else:
        lines.append(
            f"- **Not adopted** at floor {floor:.2f}: v4 refusal "
            f"{payload['baseline']['v4']['refusal_accuracy']:.4f} → "
            f"{payload['adopted']['v4']['refusal_accuracy']:.4f}, answer rate v1 "
            f"{payload['baseline']['v1']['answer_rate']:.4f} → "
            f"{payload['adopted']['v1']['answer_rate']:.4f}, v4 "
            f"{payload['baseline']['v4']['answer_rate']:.4f} → "
            f"{payload['adopted']['v4']['answer_rate']:.4f}. The rule, not the number, is the "
            "result."
        )
    lines += [
        "",
        "## v4 in full",
        "",
        "Every question on the decision set, with both signals and both decisions. Six",
        "unanswerable questions means one row is worth 0.1667 of refusal accuracy.",
        "",
        "| id | class | confidence | phrase novelty | unseen pairs | refused before | after |",
        "|---|---|---|---|---|---|---|",
    ]
    for row in payload["detailed"]["v4"]:
        pairs = ", ".join(row["unseen"]) or "-"
        lines.append(
            f"| {row['id']} | {'answerable' if row['answerable'] else 'unanswerable'} "
            f"| {row['confidence']:.3f} | {row['phrase_novelty']:.3f} | {pairs} "
            f"| {'yes' if row['refused_before'] else 'no'} "
            f"| {'yes' if row['refused_after'] else 'no'} |"
        )
    lines += [
        "",
        "## What this signal still cannot see",
        "",
        "- **Dilution.** A share falls when a question is wordy; a single unseen pair beside many",
        "  known ones cannot move the number. That is measured here, not argued.",
        "- **A topic built from attested pairs.** Two real words in their usual order can name a",
        "  thing the corpus does not have. Vocabulary, at either level, cannot refute meaning.",
        "- **Corpus size.** Both floors are fitted on 18 documents; fewer need a re-fit.",
        "",
        "## Reproducing",
        "",
        "```",
        "make phrase",
        "```",
        "",
    ]
    (RESULTS / "phrase.md").write_text("\n".join(lines), encoding="utf-8")

    print("written: results/phrase.md, results/phrase.json")
    print(f"  fitted floor {floor:.2f} (worst v1 answerable phrase novelty {worst_v1:.4f})")
    for label in ("v1", "v2", "v3", "v4"):
        before, after = payload["baseline"][label], payload["adopted"][label]
        print(f"  {label}: answer {before['answer_rate']:.4f} -> {after['answer_rate']:.4f} "
              f"| refusal {before['refusal_accuracy']:.4f} -> {after['refusal_accuracy']:.4f}")
    print(f"  adopted: {adopted}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
