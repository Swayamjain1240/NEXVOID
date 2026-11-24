"""NFR-12: the layering rules from SwDD §1, enforced on the real import graph.

`docs/05-software-design.md` states three rules and says "any PR that breaks a rule is rejected on
the rule" — but for seventeen milestones the only enforcer was a reviewer. The built module set
differs from that document's plan (`answer.py` rather than `pipeline.py`, `handler.py` rather than
the whole `api.py`), so this test checks the *intent* against the code that exists: leaves stay
leaves, ranking does not know about answering, and nothing below the boundary knows that HTTP
exists.

Parsed with `ast`, so a violation fails here even if it is only imported inside a function.
"""

from __future__ import annotations

import ast
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PKG = ROOT / "src" / "atlas"

# module (prefix) -> modules it must not import; a prefix matches the module and its children
RULES: dict[str, tuple[str, ...]] = {
    # leaves
    "atlas.config": ("atlas",),
    "atlas.interfaces": ("atlas",),
    "atlas.text": ("atlas.retrieval", "atlas.store", "atlas.answer", "atlas.guard",
                   "atlas.handler", "atlas.server", "atlas.cli"),
    # ranking knows passages, not answers
    "atlas.retrieval": ("atlas.answer", "atlas.handler", "atlas.server", "atlas.cli"),
    # persistence knows rows, not ranking
    "atlas.store": ("atlas.retrieval", "atlas.answer", "atlas.handler", "atlas.server"),
    # the request flow never imports the boundary that calls it
    "atlas.answer": ("atlas.api", "atlas.handler", "atlas.server", "atlas.cli"),
    "atlas.guard": ("atlas.answer", "atlas.handler", "atlas.server", "atlas.cli"),
    "atlas.breaker": ("atlas.handler", "atlas.server", "atlas.cli"),
    "atlas.support": ("atlas.answer", "atlas.handler", "atlas.server", "atlas.cli"),
    "atlas.select": ("atlas.handler", "atlas.server", "atlas.cli"),
    "atlas.phrase": ("atlas.answer", "atlas.handler", "atlas.server", "atlas.cli"),
    # M21: window arithmetic is a leaf; it may not reach into storage or ranking
    "atlas.versions": ("atlas.store", "atlas.retrieval", "atlas.answer", "atlas.handler",
                       "atlas.server", "atlas.cli"),
    # payload shapes depend on the data types only
    "atlas.api": ("atlas.store", "atlas.retrieval", "atlas.answer", "atlas.handler",
                  "atlas.server"),
    "atlas.metrics": ("atlas.handler", "atlas.server", "atlas.cli"),
    "atlas.obs": ("atlas.handler", "atlas.server", "atlas.cli"),
    # only the entry points may start a server
    "atlas.handler": ("atlas.server",),
}

HTTP_ALLOWED = {"atlas.handler", "atlas.server", "atlas.cli"}


def _module_name(path: Path) -> str:
    parts = list(path.relative_to(ROOT / "src").with_suffix("").parts)
    if parts[-1] == "__init__":
        parts.pop()
    return ".".join(parts)


def _imports(path: Path) -> set[str]:
    """Every atlas module this file imports, relative imports resolved to full names."""
    name = _module_name(path)
    package = name.split(".")[:-1]
    tree = ast.parse(path.read_text(encoding="utf-8"))
    found: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            found.update(a.name for a in node.names if a.name.split(".")[0] == "atlas")
        elif isinstance(node, ast.ImportFrom):
            base = package[: len(package) - (node.level - 1)] if node.level else []
            target = ".".join([*base, *(node.module.split(".") if node.module else [])])
            if target.split(".")[0] != "atlas":
                continue
            found.add(target)
            found.update(f"{target}.{a.name}" for a in node.names)
    return found


def _sources() -> list[Path]:
    return sorted(p for p in PKG.rglob("*.py") if "__pycache__" not in p.parts)


def _matches(module: str, prefix: str) -> bool:
    return module == prefix or module.startswith(prefix + ".")


def test_every_rule_holds():
    violations = []
    for path in _sources():
        module = _module_name(path)
        imports = _imports(path)
        for rule, forbidden in RULES.items():
            if not _matches(module, rule):
                continue
            for banned in forbidden:
                if banned == "atlas":
                    hits = {i for i in imports if _matches(i, "atlas")}
                else:
                    hits = {i for i in imports if _matches(i, banned)}
                if hits:
                    violations.append(f"{module} imports {sorted(hits)} (forbidden: {banned})")
    assert not violations, "\n".join(violations)


def test_http_stack_stays_at_the_boundary():
    """Only the entry points may import the HTTP server; a library that does cannot be reused."""
    offenders = [
        _module_name(p)
        for p in _sources()
        if _module_name(p) not in HTTP_ALLOWED and _imports(p) & {"http.server", "http"}
    ]
    assert not offenders, f"HTTP imported outside the boundary: {offenders}"


def test_the_rules_actually_bite():
    """A rule that matches no module is a comment; every rule must govern something."""
    governed = {_module_name(p) for p in _sources()}
    covered = {m for m in governed if any(_matches(m, rule) for rule in RULES)}
    assert covered, "no module matched any rule"
    assert _imports(PKG / "answer.py") >= {"atlas.retrieval"}, "parser found no imports at all"
