"""NFR-15: the same corpus and questions produce the same report, proved by running twice.

Until M20 this row said "each report fixes corpus, seed and encoder" — an argument, not a check.
Here the harness runs the committed eval command twice, hashes what each run wrote, and diffs the
two payloads field by field. A difference is reported by path, so a number that drifts names
itself.

Two conventions make the comparison honest rather than convenient:

- only keys that are *known* to be volatile are ignored (`captured_at`, `seconds`, …). The current
  payloads contain none of them, so today this is a byte-exact diff — if a report ever starts
  stamping a clock, the ignored key has to be named here on purpose.
- the committed reports are restored from disk afterwards, so running this probe never leaves the
  repository showing the probe's own output.

Writes results/determinism.{md,json}; exits 1 on any difference or failed run.
"""

from __future__ import annotations

import hashlib
import json
import os
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
RESULTS = ROOT / "results"
FILES = ("eval_report.json", "baseline.json")
RESTORE = (*FILES, "eval_report.md")  # the run also rewrites the human report
VOLATILE = {"captured_at", "generated_at", "timestamp", "started_at", "finished_at",
            "duration_s", "seconds", "elapsed_s"}
RUNS = 2


def _strip(node, ignored: set[str]):
    """Drop known-volatile keys at any depth, and record which ones were actually found."""
    if isinstance(node, dict):
        kept = {}
        for key, value in node.items():
            if key in VOLATILE:
                ignored.add(key)
                continue
            kept[key] = _strip(value, ignored)
        return kept
    if isinstance(node, list):
        return [_strip(item, ignored) for item in node]
    return node


def _named(keys: set[str]) -> str:
    return ", ".join(f"`{key}`" for key in sorted(keys)) if keys else "none"


def _digest(node) -> str:
    return hashlib.sha256(json.dumps(node, sort_keys=True).encode("utf-8")).hexdigest()[:16]


def _diff(left, right, path: str = "") -> list[str]:
    """Every leaf that differs, named by path, so a drift points at the number that moved."""
    if type(left) is not type(right):
        return [f"{path or '.'}: type {type(left).__name__} != {type(right).__name__}"]
    if isinstance(left, dict):
        out = []
        for key in sorted(set(left) | set(right)):
            if key not in left or key not in right:
                out.append(f"{path}.{key}: present in one run only")
            else:
                out += _diff(left[key], right[key], f"{path}.{key}")
        return out
    if isinstance(left, list):
        out = []
        if len(left) != len(right):
            out.append(f"{path}: length {len(left)} != {len(right)}")
        for index, (a, b) in enumerate(zip(left, right, strict=False)):
            out += _diff(a, b, f"{path}[{index}]")
        return out
    return [] if left == right else [f"{path}: {left!r} != {right!r}"]


def _run(command: list[str]) -> dict:
    start = time.perf_counter()
    completed = subprocess.run(
        command, cwd=ROOT, capture_output=True, text=True, timeout=900,
        env=dict(os.environ, PYTHONPATH="src"),
    )
    return {
        "command": " ".join(command),
        "exit": completed.returncode,
        "seconds": round(time.perf_counter() - start, 2),
        "tail": completed.stdout.strip().splitlines()[-2:],
        "stderr": completed.stderr.strip().splitlines()[-2:],
    }


def main() -> int:
    saved = {name: (RESULTS / name).read_bytes() for name in RESTORE if (RESULTS / name).exists()}
    runs, payloads, ignored = [], [], set()
    try:
        for index in range(RUNS):
            receipt = _run([sys.executable, "-m", "eval.run_eval"])
            receipt["index"] = index + 1
            runs.append(receipt)
            if receipt["exit"] != 0:
                break
            payloads.append(
                {name: _strip(json.loads((RESULTS / name).read_text(encoding="utf-8")), ignored)
                 for name in FILES}
            )
            print(f"[run {index + 1}] exit 0 in {receipt['seconds']}s")
    finally:
        for name, raw in saved.items():
            (RESULTS / name).write_bytes(raw)

    differences = []
    if len(payloads) == RUNS:
        for name in FILES:
            differences += _diff(payloads[0][name], payloads[1][name], name)
    else:
        differences = [f"only {len(payloads)} of {RUNS} runs completed"]
    hashes = [_digest(payload) for payload in payloads]
    passed = not differences

    payload = {
        "provenance": {
            "generator": "python -m eval.determinism",
            "runs": RUNS,
            "files": list(FILES),
            "ignored_keys": sorted(ignored),
        },
        "runs": runs,
        "hashes": hashes,
        "differences": differences,
        "verdict": "PASS" if passed else "FAIL",
    }
    (RESULTS / "determinism.json").write_text(json.dumps(payload, indent=2), encoding="utf-8")

    lines = [
        "# Determinism — two runs, one answer (NFR-15, M20)",
        "",
        "Command: `python -m eval.determinism` · the real eval command run twice, back to back,",
        "with each run's `results/eval_report.json` and `results/baseline.json` normalised, hashed",
        "and diffed field by field. The committed reports are restored afterwards.",
        "",
        "| run | command | exit | seconds | content hash |",
        "|---|---|---|---|---|",
    ]
    for index, receipt in enumerate(runs):
        digest = hashes[index] if index < len(hashes) else "—"
        lines.append(
            f"| {receipt['index']} | `{receipt['command'].split(' -m ')[-1]}` | {receipt['exit']} "
            f"| {receipt['seconds']} | `{digest}` |"
        )
    lines += [
        "",
        f"Keys ignored as volatile on purpose: {_named(ignored)}"
        + (f" — **{len(ignored)} were present**, so the diff below is not byte-exact. "
           if ignored else " — the payloads carry no clock, so this is a byte-exact comparison."),
        "",
        f"Differences: **{len(differences)}**. Verdict: **{'PASS' if passed else 'FAIL'}**.",
        "",
    ]
    if differences:
        lines += ["```", *differences[:20], "```", ""]
    else:
        lines += [
            "Every recall, precision, MRR, nDCG, refusal rate, interval bound and per-question",
            "miss list is identical across the two runs, which is what makes every other report",
            "in `results/` a reproduction rather than a transcript of one lucky afternoon.",
            "",
        ]
    (RESULTS / "determinism.md").write_text("\n".join(lines), encoding="utf-8")
    print("written: results/determinism.md, results/determinism.json "
          f"· verdict: {payload['verdict']}")
    return 0 if passed else 1


if __name__ == "__main__":
    raise SystemExit(main())
