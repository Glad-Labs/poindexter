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

Public mirror
-------------
The public mirror is this repository minus the operator-private files the sync
strips, and its CI runs this lint on that tree. A comment here may cite one of
those files. On the mirror the citation resolves to nothing and reads as dead,
though the file is alive in the source repository. The tree cannot tell a
stripped file from a deleted one, and a list of the stripped files shipped in
this lint would disclose them. That false positive held the mirror's
unit-tests job red from 2026-09-20 to 2026-09-28.

So on the mirror (``lib_public_mirror.on_public_mirror``) a reference to a path
absent from the tree is counted, not failed. Nothing is lost. The mirror
differs from the source only by the stripped files, so a reference that fails
there and passes here points at one of them. The source repository's run gates
every merge and has already checked each reference against the full tree.

Two rules keep stripped names out of the public tree:

* ``--update-baseline`` refuses on the mirror. A baseline written from the
  stripped tree would record every stripped file a comment cites, in a file
  the mirror ships.
* No baseline entry may be keyed by a stripped FILE, since the key names it.
  Fix a dead reference inside a stripped file rather than baselining it. The
  source repository's simulated-mirror check fails on such an entry.

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
from lib_public_mirror import on_public_mirror  # noqa: E402
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

    mirror = on_public_mirror()
    require_dir(SCAN_ROOT, lint="comment_reference_lint")
    if a.update_baseline and mirror:
        print("comment-reference-lint: refusing --update-baseline on the public "
              "mirror. This tree lacks the files the sync strips, so every "
              "comment citing one would enter the baseline and name that file "
              "in a file the mirror ships. Re-baseline in the source repository.",
              file=sys.stderr)
        return 2
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

    # Print FOUND and BASELINED separately. They are not the same number and
    # can legitimately differ — a tree sitting below its baseline is clean but
    # not yet locked in. Collapsing them into one figure is what made a stale
    # bandit baseline entry invisible during the 2026-08-28 CI audit (the same
    # doctrine as semgrep_lint.py).
    baselined = sum(sum(v.values()) for v in baseline.values())

    if regressions and mirror:
        # Counted, never listed: the list would be an index of the files the
        # sync strips, printed into the public repository's CI log.
        print(f"comment-reference-lint: public mirror — {len(regressions)} "
              f"reference(s) to paths absent from this tree not gated. The sync "
              f"strips operator files before publishing, so a comment here can "
              f"cite a file that exists only in the source repository, whose own "
              f"run checks every reference against the full tree ({total} found "
              f"/ {baselined} baselined across {scanned} python files scanned).")
        return 0

    if regressions:
        print("DEAD FILE REFERENCES IN COMMENTS (not in baseline):")
        print("\n".join(regressions))
        print("\nA comment naming a file that does not exist reads as current and "
              "sends the next reader somewhere the codebase abandoned. Fix the "
              "path, drop the reference, or — if the file genuinely moved and "
              "the comment is still right — re-run with --update-baseline.")
        print("\n(On a checkout of the public mirror, comments citing files the "
              "sync strips land here too. The mirror's CI tolerates them; see "
              "scripts/ci/lib_public_mirror.py.)")
        return 1

    # Not on the mirror: re-baselining is refused there (see main's guard).
    tail = ("" if total == baselined or mirror
            else "  <- re-baseline to lock the win in")
    print(f"comment-reference-lint: clean — no new dead references "
          f"({total} found / {baselined} baselined across {scanned} python "
          f"files scanned; ratchet only shrinks).{tail}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
