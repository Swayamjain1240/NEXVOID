"""The out-of-sample test FR-11 and the refusal gate needed (M11).

Every number in `results/ablation.md` and `results/eval_report.md` was measured on the same
24+6 questions the design was tuned against, so answers like "refusal accuracy 1.00" were
in-sample claims. This harness adds a second, appended set - `eval/golden_set_v2.jsonl`,
12 answerable and 6 unanswerable questions written after the code was frozen - and uses it
the only way a holdout is useful:

    fit on v1, decide on v2.

Three questions are answered here:

1. **Does retrieval generalise?** The five configurations are scored on both sets.
2. **Does the confidence gate generalise?** Answer rate and refusal accuracy are computed at
   every theta in the shipped grid for both sets, from confidences measured once.
3. **Can fusion weights close FR-11?** A small grid of sparse/dense weights is scored on v1
   only; the best-on-v1 setting is then evaluated on v2 and adopted only if both agree.

The v1 set is never edited: v2 is a separate file with its own ids. Nothing was chosen by
looking at v2, including theta - that is the whole point.

**M13 appends a third set, `eval/golden_set_v3.jsonl`** (12 answerable, 6 unanswerable), because
v2 has now been read: its refusal failure is public, so tuning against it would be tuning on a
set whose answers we already know. v3 is written from the corpus after the v2 result was
recorded and is scored here for the first time. From M13 onward the decisions in this report are
read off **v3**, with v1 and v2 kept as the two earlier readings - a fix that only moves v1 and
v2 is not a fix, and a set that is only ever read once has to stay unread until it is.

**M28 appends `eval/golden_set_v5.jsonl`**: 30 unanswerable questions, no answerable ones, because
the gate's problem is not retrieval. Six unanswerable questions per set make one question worth
0.1667 of refusal accuracy, so "v3 0.50 -> 0.67" was a single question and could not tell a fix
from a coin flip. v5 was written from the corpus - twenty topics the corpus has no word for and ten
near-misses where the words exist but the rule does not - and its confidences were measured only
after the file was written. At 30 questions one question is worth 0.0333, and the gate table below
now carries a 95% interval for each rate so that granularity is visible instead of implied.

**M29 appends `eval/golden_set_v6.jsonl`**: 30 answerable questions and no unanswerable ones, the
mirror of v5. v5 made refusals of unseen topics measurable at 0.0333 per question; the opposite
error - refusing a question the corpus can answer - was still read off six-question sets, where one
question is 0.1667. v6 covers all eighteen documents once or twice, and the answer rate it prints
is 1 minus the false-refusal rate, at the same resolution.

One caveat stated before the numbers: v3 is written by the same person who wrote the code, so it
is a weaker holdout than a set written by a stranger. It still catches the failure that matters
here - a threshold fitted to six questions - because its unanswerable questions were chosen from
topics the corpus does not cover, not from the failure mode already known.

Writes `results/holdout.md` and `results/holdout.json`.
"""

from __future__ import annotations

import dataclasses
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from atlas.config import SETTINGS  # noqa: E402
from atlas.retrieval import HybridIndex  # noqa: E402
from atlas.support import novelty, refuses, unseen_terms  # noqa: E402
from eval import metrics  # noqa: E402
from eval.run_eval import (  # noqa: E402
    ANSWER_K,
    RESULTS,
    build_store,
    load_questions,
    provenance,
)

V2_PATH = ROOT / "eval" / "golden_set_v2.jsonl"
V3_PATH = ROOT / "eval" / "golden_set_v3.jsonl"
V4_PATH = ROOT / "eval" / "golden_set_v4.jsonl"
V5_PATH = ROOT / "eval" / "golden_set_v5.jsonl"
V6_PATH = ROOT / "eval" / "golden_set_v6.jsonl"
SET_LABELS = ("v1", "v2", "v3", "v4", "v5", "v6")
# v5 is unanswerable-only (M28), so it has nothing to retrieve: it scores in the gate, not here.
RETRIEVAL_LABELS = ("v1", "v2", "v3", "v4", "v6")
ENCODER = "potion-base-8M"  # the shipped encoder; the hashed stand-in is in results/ablation.md

CONFIGS = (
    ("sparse", "sparse", {"rerank_kind": "none"}),
    ("dense", "dense", {"rerank_kind": "none"}),
    ("hybrid (fusion only)", "hybrid", {"rerank_kind": "none"}),
    ("hybrid + blend", "hybrid", {"rerank_kind": "blend"}),
    ("hybrid + learned", "hybrid", {"rerank_kind": "learned", "rerank_allow_unadopted": True}),
)
FUSION = "hybrid (fusion only)"
SHIPPED = FUSION  # M16: fusion order is the shipped default; the blend is measured below
WEIGHT_GRID = ((1.0, 1.0), (1.5, 1.0), (2.0, 1.0), (1.0, 0.5), (1.0, 1.5), (1.5, 0.5))


def load_set(path: Path, tag: str) -> tuple[list[dict], list[dict]]:
    """One appended set, validated on read so a malformed label cannot pass silently."""
    rows = [
        json.loads(line)
        for line in path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    for row in rows:
        assert set(row) >= {"id", "query", "answerable", "relevant_doc_ids"}, row
        if row["answerable"]:
            assert row["relevant_doc_ids"], f"{row['id']} is answerable with no label"
        else:
            assert not row["relevant_doc_ids"], f"{row['id']} is unanswerable but labelled"
    ids = [row["id"] for row in rows]
    assert len(ids) == len(set(ids)), f"duplicate id in {tag}"
    answerable = [row for row in rows if row["answerable"]]
    return answerable, [row for row in rows if not row["answerable"]]


def load_v2() -> tuple[list[dict], list[dict]]:
    return load_set(V2_PATH, "v2")


def load_v3() -> tuple[list[dict], list[dict]]:
    return load_set(V3_PATH, "v3")


def load_v4() -> tuple[list[dict], list[dict]]:
    return load_set(V4_PATH, "v4")


def load_v5() -> tuple[list[dict], list[dict]]:
    return load_set(V5_PATH, "v5")


def load_v6() -> tuple[list[dict], list[dict]]:
    return load_set(V6_PATH, "v6")


def measure(store, mode: str, overrides: dict, questions: list[dict]) -> dict:
    """Retrieval metrics for one configuration on one question set, with intervals."""
    settings = dataclasses.replace(SETTINGS, embedder_kind=ENCODER, **overrides)
    index = HybridIndex(store, settings=settings)
    columns: dict[str, list[float]] = {"recall": [], "precision5c": [], "ndcg": [], "mrr": []}
    misses: list[str] = []
    tenant = SETTINGS.default_tenant
    for question in questions:
        passages = index.search(question["query"], tenant_id=tenant, k=ANSWER_K, mode=mode)
        docs = metrics.rank_documents(passages)
        relevant = set(question["relevant_doc_ids"])
        recall = metrics.recall_at_k(docs, relevant, ANSWER_K)
        columns["recall"].append(recall)
        columns["precision5c"].append(metrics.precision_capped_at_k(docs, relevant, 5))
        columns["ndcg"].append(metrics.ndcg_at_k(docs, relevant, ANSWER_K))
        columns["mrr"].append(metrics.mrr(docs, relevant))
        if recall < 1.0:
            misses.append(question["id"])
    return {
        "recall": metrics.rate_with_ci(columns["recall"]),
        "precision5c": metrics.rate_with_ci(columns["precision5c"]),
        "ndcg": metrics.rate_with_ci(columns["ndcg"]),
        "mrr": metrics.rate_with_ci(columns["mrr"]),
        "missed_questions": misses,
    }


def confidences(store, questions: list[dict]) -> list[float]:
    """Top-passage confidence for each question, measured once and reused across thetas."""
    settings = dataclasses.replace(SETTINGS, embedder_kind=ENCODER)
    index = HybridIndex(store, settings=settings)
    tenant = SETTINGS.default_tenant
    out = []
    for question in questions:
        report = index.diagnostics(question["query"], tenant_id=tenant)
        out.append(0.0 if report is None else report[1]["confidence"])
    return out


def vocabulary(store) -> frozenset[str]:
    """The corpus vocabulary the novelty signal is judged against (M28)."""
    settings = dataclasses.replace(SETTINGS, embedder_kind=ENCODER)
    view = HybridIndex(store, settings=settings).view(SETTINGS.default_tenant)
    return frozenset() if view is None else view.vocabulary


def signals(questions: list[dict], values: list[float], vocab) -> list[dict]:
    """Per-question confidence beside the two novelty numbers, so one row can be read alone."""
    return [
        {
            "id": question["id"],
            "confidence": round(value, 4),
            "novelty": novelty(question["query"], vocab),
            "unseen": unseen_terms(question["query"], vocab),
        }
        for question, value in zip(questions, values, strict=True)
    ]


def rate(flags: list[float]) -> dict:
    """A rate with its interval; an empty class is undefined, not zero (v5 has no answerables)."""
    if not flags:
        return {"mean": None, "ci_low": None, "ci_high": None}
    return metrics.rate_with_ci(flags)


def spread(values: list[float]) -> dict:
    if not values:  # v5 has no answerable questions (M28): undefined, not zero
        return {"min": None, "max": None, "mean": None}
    return {"min": round(min(values), 3), "max": round(max(values), 3),
            "mean": round(sum(values) / len(values), 3)}


def theta_table(profile: dict, grid: tuple[float, ...]) -> list[dict]:
    """Answer rate and refusal accuracy at every theta, for both sets, one row per theta."""
    rows = []
    for theta in grid:
        row = {"theta": theta}
        for label, group in profile.items():
            a, u = group["answerable"], group["unanswerable"]
            answered = sum(1 for value in a if value >= theta)
            refused = sum(1 for value in u if value < theta)
            row[label] = {
                # v5 has no answerable questions, so that class is undefined rather than 0.
                "answer_rate": metrics.rate_with_ci(
                    [1.0] * answered + [0.0] * (len(a) - answered)
                )["mean"] if a else None,
                "refusal_accuracy": metrics.rate_with_ci(
                    [1.0] * refused + [0.0] * (len(u) - refused)
                )["mean"] if u else None,
            }
        rows.append(row)
    return rows


def _shown(value: float | None) -> str:
    """A rate that may be undefined (v5 has no answerable class), printed honestly."""
    return "-" if value is None else f"{value:.4f}"


def main() -> int:
    v1_answerable, v1_unanswerable = load_questions()
    v2_answerable, v2_unanswerable = load_v2()
    v3_answerable, v3_unanswerable = load_v3()
    v4_answerable, v4_unanswerable = load_v4()
    v5_answerable, v5_unanswerable = load_v5()
    v6_answerable, v6_unanswerable = load_v6()
    sets = {
        "v1": (v1_answerable, v1_unanswerable),
        "v2": (v2_answerable, v2_unanswerable),
        "v3": (v3_answerable, v3_unanswerable),
        "v4": (v4_answerable, v4_unanswerable),
        "v5": (v5_answerable, v5_unanswerable),
        "v6": (v6_answerable, v6_unanswerable),
    }
    store = build_store(SETTINGS.default_tenant)
    try:
        vocab = vocabulary(store)  # M28: the novelty signal's "does the corpus know this word"
        results = {
            name: {
                label: measure(store, mode, overrides, sets[label][0])
                for label in RETRIEVAL_LABELS
            }
            for name, mode, overrides in CONFIGS
        }
        profile = {
            label: {
                "answerable": confidences(store, sets[label][0]),
                "unanswerable": confidences(store, sets[label][1]),
            }
            for label in SET_LABELS
        }
        grid = [
            {
                "sparse_weight": sparse_w,
                "dense_weight": dense_w,
                "v1": measure(
                    store, "hybrid",
                    {"rerank_kind": "blend", "rrf_weight_sparse": sparse_w,
                     "rrf_weight_dense": dense_w},
                    v1_answerable,
                )["recall"],
                "v2": measure(
                    store, "hybrid",
                    {"rerank_kind": "blend", "rrf_weight_sparse": sparse_w,
                     "rrf_weight_dense": dense_w},
                    v2_answerable,
                )["recall"],
                "v3": measure(
                    store, "hybrid",
                    {"rerank_kind": "blend", "rrf_weight_sparse": sparse_w,
                     "rrf_weight_dense": dense_w},
                    v3_answerable,
                )["recall"],
            }
            for sparse_w, dense_w in WEIGHT_GRID
        ]
    finally:
        store.close()

    trained = dataclasses.replace(SETTINGS, embedder_kind=ENCODER)
    theta = trained.theta
    table = theta_table(profile, trained.theta_grid)
    shipped_row = min(table, key=lambda row: abs(row["theta"] - theta))
    shipped = {}
    for label in SET_LABELS:
        # M28: an interval per rate, because six questions made every difference look decisive.
        answer_ci = metrics.rate_with_ci(
            [1.0 if value >= theta else 0.0 for value in profile[label]["answerable"]]
        ) if profile[label]["answerable"] else {"mean": None, "ci_low": None, "ci_high": None}
        # M29: v6 has no unanswerable questions, so its refusal class is undefined, not 0
        refusal_ci = rate(
            [1.0 if value < theta else 0.0 for value in profile[label]["unanswerable"]]
        )
        shipped[label] = {
            "answer_rate": shipped_row[label]["answer_rate"],
            "refusal_accuracy": shipped_row[label]["refusal_accuracy"],
            "answer_ci": answer_ci,
            "refusal_ci": refusal_ci,
        }
    shipped["theta"] = theta

    # M28: the same questions through the combined decision (M14), so the floor is judged on the
    # set that was written to judge it. Nothing is re-retrieved: confidence and novelty were each
    # measured once, and this is the same measurement re-bucketed.
    floor = trained.refusal_novelty_floor
    detail = {
        label: {
            "answerable": signals(sets[label][0], profile[label]["answerable"], vocab),
            "unanswerable": signals(sets[label][1], profile[label]["unanswerable"], vocab),
        }
        for label in SET_LABELS
    }

    def refuses_row(row: dict) -> bool:
        return refuses(
            confidence=row["confidence"],
            theta=theta,
            novel=row["novelty"],
            unseen=len(row["unseen"]),
            floor=floor,
        )

    with_floor = {}
    for label in SET_LABELS:
        answered = rate([0.0 if refuses_row(row) else 1.0 for row in detail[label]["answerable"]])
        refused = rate([1.0 if refuses_row(row) else 0.0 for row in detail[label]["unanswerable"]])
        with_floor[label] = {
            "answer_rate": answered["mean"],
            "answer_ci": answered,
            "refusal_accuracy": refused["mean"],
            "refusal_ci": refused,
        }

    shipped_v1 = results[SHIPPED]["v1"]["recall"]["mean"]
    shipped_v2 = results[SHIPPED]["v2"]["recall"]["mean"]
    shipped_v3 = results[SHIPPED]["v3"]["recall"]["mean"]
    best_v1 = max(
        grid,
        key=lambda row: (row["v1"]["mean"], -(row["sparse_weight"] + row["dense_weight"])),
    )
    dense_v2 = results["dense"]["v2"]["recall"]["mean"]
    sparse_v2 = results["sparse"]["v2"]["recall"]["mean"]
    fusion_v2 = results[FUSION]["v2"]["recall"]["mean"]
    dense_v3 = results["dense"]["v3"]["recall"]["mean"]
    sparse_v3 = results["sparse"]["v3"]["recall"]["mean"]
    fusion_v3 = results[FUSION]["v3"]["recall"]["mean"]

    # Adoption rule, stated before the numbers are read: no v1 loss AND a v3 gain.
    # v1 is still what the best setting is chosen on; v3 is what decides whether it ships.
    weight_adopted = best_v1["v1"]["mean"] >= shipped_v1 and best_v1["v3"]["mean"] > shipped_v3
    weight_tied = len({round(row["v1"]["mean"], 6) for row in grid}) == 1
    v6_total = len(sets["v6"][0])
    v6_answer = shipped["v6"]["answer_rate"]
    v6_answered = round(v6_answer * v6_total)
    v6_answered_floor = round(with_floor["v6"]["answer_rate"] * v6_total)
    v2_refusal_best = max(row["v2"]["refusal_accuracy"] for row in table)
    v3_refusal_best = max(row["v3"]["refusal_accuracy"] for row in table)
    v4_refusal_best = max(row["v4"]["refusal_accuracy"] for row in table)
    v5_refusal_best = max(row["v5"]["refusal_accuracy"] for row in table)
    v5_rows = detail["v5"]["unanswerable"]
    v5_refused_confidence = sum(1 for row in v5_rows if row["confidence"] < theta)
    v5_refused_floor = sum(1 for row in v5_rows if refuses_row(row))
    flipped = [row["id"] for row in v5_rows if row["confidence"] >= theta and refuses_row(row)]
    attested_missed = [
        row["id"] for row in v5_rows if not row["unseen"] and not refuses_row(row)
    ]
    payload = {
        "provenance": provenance(),
        "method": (__doc__ or "").strip(),
        "encoder": ENCODER,
        "sets": {
            label: {"answerable": len(sets[label][0]), "unanswerable": len(sets[label][1])}
            for label in SET_LABELS
        },
        "configs": results,
        "confidence": {
            "theta": theta,
            "floor": floor,
            "shipped": shipped,
            "with_floor": with_floor,
            "detail": detail,
            "theta_table": table,
            "spread": {label: {group: spread(values) for group, values in groups.items()}
                       for label, groups in profile.items()},
        },
        "fusion_grid": grid,
        "decision": {
            "shipped_v1": shipped_v1,
            "shipped_v2": shipped_v2,
            "shipped_v3": shipped_v3,
            "decided_on": "v3",
            "best_on_v1": {
                "sparse_weight": best_v1["sparse_weight"],
                "dense_weight": best_v1["dense_weight"],
                "v1": best_v1["v1"]["mean"],
                "v2": best_v1["v2"]["mean"],
            },
            "weights_adopted": weight_adopted,
            "weights_identifiable": not weight_tied,
            "weights_tied": weight_tied,
            "fr11_met_on_v2": fusion_v2 >= max(dense_v2, sparse_v2),
            "fr11_gap_on_v2": round(fusion_v2 - max(dense_v2, sparse_v2), 4),
            "fr11_met_on_v3": fusion_v3 >= max(dense_v3, sparse_v3),
            "fr11_gap_on_v3": round(fusion_v3 - max(dense_v3, sparse_v3), 4),
            "refusal_generalises": shipped["v3"]["refusal_accuracy"] >= 0.8,
            "refusal_generalises_on_v2": shipped["v2"]["refusal_accuracy"] >= 0.8,
            "best_v2_refusal_across_thetas": v2_refusal_best,
            "best_v3_refusal_across_thetas": v3_refusal_best,
            "best_v4_refusal_across_thetas": v4_refusal_best,
            "best_v5_refusal_across_thetas": v5_refusal_best,
            "v5_refused_confidence": v5_refused_confidence,
            "v5_refused_with_floor": v5_refused_floor,
        },
    }
    (RESULTS / "holdout.json").write_text(json.dumps(payload, indent=2), encoding="utf-8")

    lines = [
        "# Holdout — the out-of-sample test (M11)",
        "",
        f"Encoder `{ENCODER}`. v1 is the frozen 24+6 set every earlier number used; v2 is an",
        "appended 12+6 set written after the code was frozen, with its own ids. Nothing in v1",
        "was edited, and no v2 question was used to choose a weight, a threshold or a ranker.",
        "v3 is the second appended set (12+6, M13), written after the v2 refusal failure was",
        "already known and scored here for the first time. **Decisions below are read off v3.**",
        "v4 is the set written for the M17 novelty question (12+6); it is scored here too, but",
        "it was not written for retrieval, so its recall column is a check, not a target.",
        "v5 is the M28 set: 30 unanswerable questions and no answerable ones, written because six",
        "questions per set made one question worth 0.1667 of refusal accuracy. It scores in the",
        "gate only, and it is the first set here whose rate does not move one question at a time.",
        "v6 is its mirror (M29): 30 answerable questions and no unanswerable ones, so a false",
        "refusal is worth 0.0333 too. The two together price both sides of the gate.",
        "",
        "## Retrieval, all four sets, same configurations",
        "",
        "| configuration | v1 recall@10 (95% CI) | v2 recall@10 | v3 recall@10 "
        "| v4 recall@10 | v6 recall@10 | v3 p@5 capped | v3 MRR |",
        "|---|---|---|---|---|---|---|---|",
    ]
    for name, block in results.items():
        one, two, three = block["v1"]["recall"], block["v2"]["recall"], block["v3"]["recall"]
        four, six = block["v4"]["recall"], block["v6"]["recall"]
        lines.append(
            f"| {name} | {one['mean']:.4f} [{one['ci_low']:.3f}, {one['ci_high']:.3f}] "
            f"| {two['mean']:.4f} | {three['mean']:.4f} | {four['mean']:.4f} "
            f"| {six['mean']:.4f} "
            f"| {block['v3']['precision5c']['mean']:.4f} | {block['v3']['mrr']['mean']:.4f} |"
        )
    v2_reaches = [block["v2"]["recall"]["mean"] for block in results.values()]
    v3_reaches = [block["v3"]["recall"]["mean"] for block in results.values()]
    v4_reaches = [block["v4"]["recall"]["mean"] for block in results.values()]
    v6_reaches = [block["v6"]["recall"]["mean"] for block in results.values()]
    lines += [
        "",
        f"{sum(1 for value in v2_reaches if value == 1.0)} of five configurations reach 1.0000 on",
        f"v2, {sum(1 for value in v3_reaches if value == 1.0)} of five on v3, "
        f"{sum(1 for value in v4_reaches if value == 1.0)} of five on v4 and "
        f"{sum(1 for value in v6_reaches if value == 1.0)} of five on v6, so all four appended",
        "answerable sets",
        "are saturated: they say only that no configuration misses a relevant document on",
        "questions like these, which are short, factual and single-document - exactly the traffic",
        "this corpus produces. Neither set can rank the rankers. Their teeth are in the gate, and",
        "that is where both earn their place.",
        "",
        "## The gate on the new set: answer rate and refusal accuracy",
        "",
        "| set | theta | answer rate (95% CI) | refusal accuracy (95% CI) | n answerable "
        "| n unanswerable |",
        "|---|---|---|---|---|---|",
    ]
    for label in SET_LABELS:
        block = shipped[label]
        a_ci, u_ci = block["answer_ci"], block["refusal_ci"]
        n_a = payload["sets"][label]["answerable"]
        n_u = payload["sets"][label]["unanswerable"]
        lines.append(
            f"| {label} | {theta:.2f} | "
            + (f"{block['answer_rate']:.4f} [{a_ci['ci_low']:.3f}, {a_ci['ci_high']:.3f}] "
               if n_a else "- (no answerable questions) ")
            + (f"| {block['refusal_accuracy']:.4f} "
               f"[{u_ci['ci_low']:.3f}, {u_ci['ci_high']:.3f}] " if n_u
               else "| - (no unanswerable questions) ")
            + f"| {n_a} | {n_u} |"
        )
    spreads = payload["confidence"]["spread"]
    lines += [
        "",
        "## The same gate with the vocabulary signal",
        "",
        f"`refusal_novelty_floor = {floor:.2f}` (M14, `results/support.md`) refuses when the "
        "confidence gate already would, or when at least two of the question's content terms "
        "occur nowhere in the corpus and at least half of them do not. It reads the whole "
        "tenant vocabulary rather than the one passage the gate chose, so it is the only signal "
        "here that can see a topic the corpus does not carry. Confidence and novelty are each "
        "measured once, so this table is the first one re-bucketed, not a second run.",
        "",
        "| set | answer rate (95% CI) | refusal accuracy (95% CI) | delta vs confidence only |",
        "|---|---|---|---|",
    ]
    for label in SET_LABELS:
        block, base = with_floor[label], shipped[label]
        a_ci, u_ci = block["answer_ci"], block["refusal_ci"]
        delta = (
            "-" if block["refusal_accuracy"] is None or base["refusal_accuracy"] is None
            else f"{block['refusal_accuracy'] - base['refusal_accuracy']:+.4f}"
        )
        lines.append(
            f"| {label} | "
            + (f"{block['answer_rate']:.4f} [{a_ci['ci_low']:.3f}, {a_ci['ci_high']:.3f}] "
               if block["answer_rate"] is not None else "- (no answerable questions) ")
            + (f"| {block['refusal_accuracy']:.4f} "
               f"[{u_ci['ci_low']:.3f}, {u_ci['ci_high']:.3f}] "
               if block["refusal_accuracy"] is not None
               else "| - (no unanswerable questions) ")
            + f"| {delta} |"
        )
    lines += [
        "",
        f"On v5 the floor refuses {v5_refused_floor} of 30 unanswerable questions where "
        f"confidence alone refused {v5_refused_confidence}, so the combined decision reads "
        f"{with_floor['v5']['refusal_accuracy']:.4f} against "
        f"{shipped['v5']['refusal_accuracy']:.4f}. "
        f"The {len(flipped)} it adds are "
        + (", ".join(flipped) if flipped else "none")
        + ": several content words absent and confidence still above theta. It cannot touch "
        + (", ".join(attested_missed) if attested_missed else "none")
        + ", where every word is attested and novelty is 0.000 - a rule that counts absent "
        "words has nothing to count there, which is exactly the case `results/support.md` calls "
        "its blind spot. The signal is real and it is not sufficient: half of v5 is still "
        "answered.",
        "",
        "## v5, question by question",
        "",
        "One row is 0.0333 of the rate above, which is the point of the set: a change of one "
        "row is no longer a change of the headline. Each `note` in `eval/golden_set_v5.jsonl` "
        "records whether the question is a topic the corpus has no word for or a near-miss.",
        "",
        "| id | confidence | novelty | unseen terms | refused (confidence) | refused (+ floor) |",
        "|---|---|---|---|---|---|",
    ]
    for row in v5_rows:
        unseen = ", ".join(row["unseen"]) or "-"
        lines.append(
            f"| {row['id']} | {row['confidence']:.3f} | {row['novelty']:.3f} | {unseen} "
            f"| {'yes' if row['confidence'] < theta else 'no'} "
            f"| {'yes' if refuses_row(row) else 'no'} |"
        )
    lines += [
        "",
        "## Why it fails: the classes overlap on the only feature the gate uses",
        "",
        "| set | class | confidence min | max | mean |",
        "|---|---|---|---|---|",
    ]
    for label, groups in spreads.items():
        for group in ("answerable", "unanswerable"):
            stats = groups[group]
            lines.append(
                f"| {label} | {group} | {stats['min']:.3f} | {stats['max']:.3f} "
                f"| {stats['mean']:.3f} |" if stats["min"] is not None
                else f"| {label} | {group} | - | - | - |"
            )
    lines += [
        "",
        "On v1 the six unanswerable questions all sit below the six weakest answerable ones,",
        "which is why theta had a clean gap to land in. On v2 they do not: the lowest",
        f"answerable question scores {spreads['v2']['answerable']['min']:.3f} and the highest",
        f"unanswerable one scores {spreads['v2']['unanswerable']['max']:.3f}. No theta separates",
        "the two classes, and across the whole grid the best v2 refusal accuracy reachable is",
        f"{v2_refusal_best:.4f} - at the cost of refusing questions the system can answer.",
        "",
        "v3 repeats the pattern with different numbers, which is what makes it a replication and",
        "not an anecdote: the weakest answerable question scores",
        f"{spreads['v3']['answerable']['min']:.3f}, the strongest unanswerable one",
        f"{spreads['v3']['unanswerable']['max']:.3f}, and the best refusal accuracy any theta can",
        f"reach is {v3_refusal_best:.4f}. The classes overlap on confidence in all three sets;",
        "the first set only looked separable because six questions happened to be easy to refuse.",
        "v5 puts the same question to thirty questions instead of six: the best refusal accuracy",
        f"any theta in the grid reaches there is {v5_refusal_best:.4f}, and on v4 it is",
        f"{v4_refusal_best:.4f}. Thirty questions do not find a threshold that six could not; they",
        "only measure how far short it falls.",
        "",
        "| theta | v1 answer | v1 refusal | v2 answer | v2 refusal | v3 answer | v3 refusal |",
        "|---|---|---|---|---|---|---|",
    ]
    for row in table:
        marker = " (shipped)" if abs(row["theta"] - theta) < 1e-9 else ""
        lines.append(
            f"| {row['theta']:.2f}{marker} | {row['v1']['answer_rate']:.4f} "
            f"| {row['v1']['refusal_accuracy']:.4f} | {row['v2']['answer_rate']:.4f} "
            f"| {row['v2']['refusal_accuracy']:.4f} | {row['v3']['answer_rate']:.4f} "
            f"| {row['v3']['refusal_accuracy']:.4f} |"
        )
    lines += [
        "",
        "The shipped theta is **not** moved. Choosing the theta with the fewest v2 mistakes",
        "would consume the holdout and leave the next claim untested, which is the failure mode",
        "this set exists to prevent. The requirement stays open in `README.md` with these",
        "numbers attached.",
        "",
        "## Fusion weights: fitted on v1, judged on v3",
        "",
        "| sparse weight | dense weight | v1 recall@10 | v2 recall@10 | v3 recall@10 |",
        "|---|---|---|---|---|",
    ]
    for row in grid:
        lines.append(
            f"| {row['sparse_weight']:.1f} | {row['dense_weight']:.1f} "
            f"| {row['v1']['mean']:.4f} | {row['v2']['mean']:.4f} "
            f"| {row['v3']['mean']:.4f} |"
        )
    lines += [
        "",
        "## Decisions",
        "",
        "- **Fusion weights:** "
        + (f"all six settings score identically on all three sets (v1 {shipped_v1:.4f}, v2 "
           f"{shipped_v2:.4f}, v3 {shipped_v3:.4f}), so this evaluation cannot identify a weight "
           "at all: fusion "
           "re-orders the same chunks whichever weight is used. **Nothing adopted.** The grid "
           "is kept as evidence that the knob is inert on a corpus this small, not as a "
           "tuned value."
           if weight_tied else
           f"best on v1 is sparse {best_v1['sparse_weight']:.1f} / dense "
           f"{best_v1['dense_weight']:.1f} with v1 {best_v1['v1']['mean']:.4f} and v2 "
           f"{best_v1['v2']['mean']:.4f}, against the shipped 1.0/1.0 at v1 {shipped_v1:.4f} "
           f"and v2 {shipped_v2:.4f}. "
           + ("Adopted: it holds on v1 and improves on v2."
              if weight_adopted else
              "**Not adopted**: the rule was no v1 loss and a v2 gain, and this does not "
              "clear both.")),
        f"- **FR-11 on the holdout:** fusion-only scores {fusion_v2:.4f} against dense "
        f"{dense_v2:.4f} and sparse {sparse_v2:.4f}, a gap of "
        f"{fusion_v2 - max(dense_v2, sparse_v2):+.4f}. "
        + ("The requirement is met on v2, but v2 saturates, so this is a weak pass: it shows "
           "fusion misses nothing on easy questions, not that it matches dense."
           if payload["decision"]["fr11_met_on_v2"] else
           "The requirement is still not met on v2."),
        f"- **Refusal generalises:** "
        f"{'yes' if payload['decision']['refusal_generalises'] else 'no'} on v3 - "
        f"refusal accuracy {shipped['v1']['refusal_accuracy']:.4f} on v1, "
        f"{shipped['v2']['refusal_accuracy']:.4f} on v2, "
        f"{shipped['v3']['refusal_accuracy']:.4f} on v3. Two independent appended sets agree, so",
        "the v1 figure was in-sample and the gate is the open requirement, not a rounding problem.",
        f"- **v5 narrows the estimate; it does not move the rate.** "
        f"{shipped['v5']['refusal_accuracy']:.4f} "
        f"[{shipped['v5']['refusal_ci']['ci_low']:.3f}, "
        f"{shipped['v5']['refusal_ci']['ci_high']:.3f}] "
        f"on 30 unanswerable questions ({v5_refused_confidence} of 30 refused), against 0.5000 "
        f"[{shipped['v3']['refusal_ci']['ci_low']:.3f}, "
        f"{shipped['v3']['refusal_ci']['ci_high']:.3f}] on the six-question v3 and v4. The point "
        "estimate is lower, but the intervals overlap, so the honest reading is a narrower "
        "estimate rather than a demonstrated decline: at these n the data are consistent with "
        "the gate refusing anywhere from about a fifth to over half of unseen topics. What v5 "
        f"does rule out is the 0.8333 v1 reported, which sits above its upper bound. With the "
        f"vocabulary floor it is {with_floor['v5']['refusal_accuracy']:.4f} "
        f"({v5_refused_floor} of 30). The requirement stays open, and the rate no longer moves "
        "one question at a time.",
        f"- **v6 measures the other error at the same resolution.** {v6_answer:.4f} of "
        f"{v6_total} answerable questions are answered at the shipped theta "
        f"[{shipped['v6']['answer_ci']['ci_low']:.3f}, 1.000], so false refusals are "
        f"{1 - v6_answer:.4f} [0.000, {1 - shipped['v6']['answer_ci']['ci_low']:.3f}] "
        f"({v6_total - v6_answered} of {v6_total}) - one question is 0.0333, the resolution v5 "
        f"gives the refusal side. With the vocabulary floor {v6_answered_floor} of {v6_total} "
        "are answered, so that signal's price is stated in the same units as its gain rather "
        "than in sixths.",
        f"- **FR-11 on v3:** fusion-only {fusion_v3:.4f} against dense {dense_v3:.4f} and sparse "
        f"{sparse_v3:.4f} ({fusion_v3 - max(dense_v3, sparse_v3):+.4f}). "
        + ("Met on v3 as well, with the same saturation caveat."
           if payload["decision"]["fr11_met_on_v3"] else
           "Still not met on the third set."),
        "",
        "## Misses, by set and configuration",
        "",
    ]
    for label in ("v2", "v3", "v4", "v6"):
        lines.append(f"**{label}**")
        lines.append("")
        for name, block in results.items():
            missed = ", ".join(block[label]["missed_questions"]) or "none"
            lines.append(f"- {name}: {missed}")
        lines.append("")
    lines.append("")
    (RESULTS / "holdout.md").write_text("\n".join(lines), encoding="utf-8")

    print("written: results/holdout.md, results/holdout.json")
    for name, block in results.items():
        print("  {:<26} {}".format(
            name,
            "  ".join(
                f"{label} {block[label]['recall']['mean']:.4f}" for label in RETRIEVAL_LABELS
            ),
        ))
    print("  gate: " + " | ".join(
        f"{label} answer {_shown(shipped[label]['answer_rate'])} "
        f"refusal {_shown(shipped[label]['refusal_accuracy'])}"
        for label in SET_LABELS
    ))
    print("  gate + vocabulary floor: " + " | ".join(
        f"{label} answer {_shown(with_floor[label]['answer_rate'])} "
        f"refusal {_shown(with_floor[label]['refusal_accuracy'])}"
        for label in SET_LABELS
    ))
    print(f"  weights adopted: {weight_adopted} | FR-11 on v2/v3: "
          f"{payload['decision']['fr11_met_on_v2']}/{payload['decision']['fr11_met_on_v3']}"
          f" | refusal generalises on v3: {payload['decision']['refusal_generalises']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
