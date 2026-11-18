"""NFR-13: every requirement in the SRS points at evidence that exists.

Answered in one sentence per id — "requirement X is backed by Y" — except the sentence is a test.
A requirement counts as backed only when its id is named in `tests/`, in a committed report under
`results/`, in an ADR, or in `results/EVIDENCE.md`, whose rows are then followed to a file that must
exist on disk. So the index cannot claim a report it does not have.

Requirements that describe a variant this project deliberately did not build are listed in
`EXEMPT` with the reason, rather than being left to look backed by silence.
"""

from __future__ import annotations

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SRS = ROOT / "docs" / "07-software-requirements.md"
EVIDENCE = ROOT / "results" / "EVIDENCE.md"

# Named gaps: each one is a requirement the MVP does not implement, and why it is not a defect.
EXEMPT = {
    "NFR-02": "stage 2 (LLM generation) is not built; its budget cannot be measured on the MVP",
    "NFR-06": "faithfulness needs a generative answer; extractive answers are verbatim passages",
}

ID = re.compile(r"\b(?:FR|NFR)-\d{2}\b")
EVIDENCE_ROW = re.compile(r"^\|\s*((?:FR|NFR)-\d{2})\s*\|[^|]*\|\s*`([^`]+)`\s*\|", re.MULTILINE)


def _ids() -> set[str]:
    return set(ID.findall(SRS.read_text(encoding="utf-8")))


def _sources() -> dict[str, str]:
    corpus = {}
    for folder in ("tests", "docs/adr"):
        for path in sorted((ROOT / folder).glob("*.md" if folder.endswith("adr") else "*.py")):
            corpus[str(path.relative_to(ROOT))] = path.read_text(encoding="utf-8")
    for path in sorted((ROOT / "results").glob("*.md")):
        corpus[str(path.relative_to(ROOT))] = path.read_text(encoding="utf-8")
    return corpus


def test_the_index_points_at_files_that_exist():
    rows = EVIDENCE_ROW.findall(EVIDENCE.read_text(encoding="utf-8"))
    assert len(rows) >= 5, "the evidence index is empty"
    missing = [f"{rid} -> {path}" for rid, path in rows if not (ROOT / path).exists()]
    assert not missing, f"EVIDENCE.md cites files that do not exist: {missing}"
    unknown = [rid for rid, _ in rows if rid not in _ids()]
    assert not unknown, f"EVIDENCE.md cites ids absent from the SRS: {unknown}"


def test_every_requirement_is_backed():
    ids = _ids()
    assert len(ids) >= 40, f"only found {len(ids)} requirement ids; the SRS parse is wrong"
    sources = _sources()
    unbacked = [
        rid
        for rid in sorted(ids)
        if rid not in EXEMPT
        and not any(re.search(rf"\b{rid}\b", text) for text in sources.values())
    ]
    assert not unbacked, (
        "requirements with no test, report, ADR or EVIDENCE.md row: "
        f"{unbacked}\nAdd the id where its evidence lives, or record it in EXEMPT with a reason."
    )


def test_exemptions_are_justified_and_current():
    ids = _ids()
    stale = [rid for rid in EXEMPT if rid not in ids]
    thin = [rid for rid, why in EXEMPT.items() if len(why.split()) < 6]
    assert not stale, f"EXEMPT names ids that no longer exist: {stale}"
    assert not thin, f"EXEMPT reasons too short to justify a gap: {thin}"
