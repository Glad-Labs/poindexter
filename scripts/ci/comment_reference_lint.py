#!/usr/bin/env python3
"""Ratchet: file paths cited in code comments must still exist.

A comment that names a file which was deleted or moved is worse than no
comment. It reads as current, it is quoted in review, and it sends the next
reader — human or agent — to a conclusion the codebase abandoned. That is not
hypothetical here: ``schemas/video_shot_list.py`` carried a comment describing
the no-AI-humans policy and citing its rationale long after that policy was
deliberately reversed (2026-09-14). Reading it as live house rule produced a
recommendation that would have re-introduced the retired ban (#3892).

Scope is deliberately narrow, because the goal is a gate that can be trusted
rather than a thorough one that gets muted:

* only PATH-shaped tokens inside comments and docstrings — a thing whose
  existence is mechanically checkable;
* a path resolves if it exists verbatim, OR if its basename exists exactly
  once anywhere in the tree (that is a moved file, still findable, not a lie);
* anything that looks like a placeholder (``services/x.py``), a URL, or a
  path outside the repo's own directories is ignored.

Same doctrine as ``bandit_lint`` / ``semgrep_lint``: grandfather what is here
today, block net-new, file nothing. The ratchet only shrinks.

    python scripts/ci/comment_reference_lint.py
    python scripts/ci/comment_reference_lint.py --update-baseline
"""
from __future__ import annotations

import argparse
import ast
import json
import os
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from lib_scan_floor import require_dir, require_scanned  # noqa: E402

REPO_ROOT = Path(__file__).resolve().parents[2]
SCAN_ROOT = REPO_ROOT / "src" / "cofounder_agent"
BASELINE_PATH = Path(__file__).resolve().parent / "comment_reference_baseline.json"

# Top-level directories a cited path may live under. A token that starts with
# anything else is prose, not a reference.
_ROOTS = (
    "src", "docs", "scripts", "infrastructure", "web", "mcp-server",
    "poindexter", "services", "modules", "plugins", "utils", "routes",
    "schemas", "config", "tasks", "brain", "cli", "skills", "tests",
)
_EXTS = (".py", ".md", ".sql", ".json", ".yml", ".yaml", ".toml", ".sh",
         ".ts", ".tsx", ".js", ".jsx", ".ini", ".cfg")

# Cited inside backticks (``x`` or `x`) — the convention this codebase uses for
# a real reference. Bare prose mentions are not scanned: too noisy to gate on.
_REF_RE = re.compile(r"``?([A-Za-z0-9_][A-Za-z0-9_./-]*(?:" +
                     "|".join(re.escape(e) for e in _EXTS) + r"))``?")

# Obvious stand-ins that are illustrations, not references.
_PLACEHOLDER = re.compile(
    r"(^|/)(x|y|z|foo|bar|baz|name|example|something|module|mymodule)\.[a-z]+$")

_SKIP_DIRS = {"__pycache__", ".git", "node_modules", ".venv", "venv", ".mypy_cache"}


def iter_comment_text(path: Path):
    """Every comment and docstring in one file, as raw strings."""
    try:
        src = path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return
    for line in src.splitlines():
        i = line.find("#")
        # crude but sufficient: ignore a '#' that sits inside an odd number of
        # quotes on that line, which is the common false positive.
        if i >= 0 and line[:i].count('"') % 2 == 0 and line[:i].count("'") % 2 == 0:
            yield line[i:]
    try:
        tree = ast.parse(src)
    except SyntaxError:
        return
    for node in ast.walk(tree):
        if isinstance(node, (ast.Module, ast.ClassDef, ast.FunctionDef,
                             ast.AsyncFunctionDef)):
            doc = ast.get_docstring(node)
            if doc:
                yield doc


def build_index(root: Path) -> tuple[set[str], dict[str, int]]:
    """Every tracked path, plus how many files share each basename."""
    paths: set[str] = set()
    basenames: dict[str, int] = {}
    for dirpath, dirs, files in os.walk(root):
        dirs[:] = [d for d in dirs if d not in _SKIP_DIRS]
        for fn in files:
            rel = (Path(dirpath) / fn).relative_to(root).as_posix()
            paths.add(rel)
            basenames[fn] = basenames.get(fn, 0) + 1
    return paths, basenames


def is_reference(tok: str) -> bool:
    if "://" in tok or tok.startswith(("http", "www.")):
        return False
    if _PLACEHOLDER.search(tok):
        return False
    if "/" in tok:
        return tok.split("/", 1)[0] in _ROOTS or tok.startswith(_ROOTS)
    # A bare filename counts only when it is distinctive enough to be a real
    # reference rather than a word that happens to end in an extension.
    return tok.endswith(_EXTS) and len(tok) > 6


def resolves(tok: str, paths: set[str], basenames: dict[str, int]) -> bool:
    cand = tok.lstrip("./")
    if cand in paths:
        return True
    if any(p.endswith("/" + cand) for p in paths):
        return True
    # A moved file is still findable when its basename is unique.
    return basenames.get(os.path.basename(cand), 0) >= 1


def scan() -> tuple[dict[str, dict[str, int]], int]:
    paths, basenames = build_index(REPO_ROOT)
    findings: dict[str, dict[str, int]] = {}
    scanned = 0
    for dirpath, dirs, files in os.walk(SCAN_ROOT):
        dirs[:] = [d for d in dirs if d not in _SKIP_DIRS]
        for fn in files:
            if not fn.endswith(".py"):
                continue
            p = Path(dirpath) / fn
            rel = p.relative_to(REPO_ROOT).as_posix()
            scanned += 1
            for blob in iter_comment_text(p):
                for tok in _REF_RE.findall(blob):
                    if not is_reference(tok):
                        continue
                    if resolves(tok, paths, basenames):
                        continue
                    findings.setdefault(rel, {})
                    findings[rel][tok] = findings[rel].get(tok, 0) + 1
    return findings, scanned


def load_baseline() -> dict[str, dict[str, int]]:
    if not BASELINE_PATH.exists():
        return {}
    return json.loads(BASELINE_PATH.read_text(encoding="utf-8")).get("files", {})


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--update-baseline", action="store_true")
    a = ap.parse_args()

    require_dir(SCAN_ROOT, lint="comment_reference_lint")
    findings, scanned = scan()
    total = sum(sum(v.values()) for v in findings.values())

    if a.update_baseline:
        BASELINE_PATH.write_text(
            json.dumps({
                "_comment": "Dead file references inside code comments, "
                            "grandfathered. The ratchet only shrinks — fix one, "
                            "re-run with --update-baseline to lock the win in.",
                "files": {k: dict(sorted(v.items())) for k, v in sorted(findings.items())},
            }, indent=2, sort_keys=False) + "\n",
            encoding="utf-8")
        print(f"comment-reference-lint: baseline written "
              f"({total} refs across {len(findings)} files)")
        return 0

    baseline = load_baseline()
    regressions: list[str] = []
    for rel, refs in sorted(findings.items()):
        allowed = baseline.get(rel, {})
        for tok, n in sorted(refs.items()):
            if n > allowed.get(tok, 0):
                regressions.append(f"  {rel}: `{tok}` does not exist "
                                   f"({n} mention(s), baseline allows "
                                   f"{allowed.get(tok, 0)})")

    require_scanned(scanned, lint="comment_reference_lint",
                    what="python files", roots=(SCAN_ROOT,))

    if regressions:
        print("DEAD FILE REFERENCES IN COMMENTS (not in baseline):")
        print("\n".join(regressions))
        print("\nA comment naming a file that does not exist reads as current and "
              "sends the next reader somewhere the codebase abandoned. Fix the "
              "path, drop the reference, or — if the file genuinely moved and "
              "the comment is still right — re-run with --update-baseline.")
        return 1

    print(f"comment-reference-lint: clean — no new dead references "
          f"({total} baselined across {len(findings)} files, "
          f"{scanned} python files scanned; ratchet only shrinks).")
    return 0


if __name__ == "__main__":
    sys.exit(main())
