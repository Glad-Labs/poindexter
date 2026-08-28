#!/usr/bin/env python3
"""CI lint: atoms are independent nodes — no atom imports a stage or a sibling atom.

The content pipeline runs as a DB-stored ``graph_def`` whose nodes reference
atoms (``modules/content/atoms/``) by name. An architect LLM composes those
atoms into ad-hoc graphs, so the graph_def must be the *complete* truth of what
executes. That holds only if every atom is an independent node: it may depend
**downward** on utilities/libraries, but it must NOT reference

  - a **stage** (``modules.content.stages.*`` — the retiring pre-atom
    orchestration layer), or
  - a **sibling atom** (another graph node).

If atom A imports atom B, placing A on a graph silently runs B too — with B's
``requires``/``produces`` contract invisible to the graph. That is the exact
composability break this lint prevents.

Node vs library — the registry's own contract
----------------------------------------------
``services/atom_registry.py`` discovers atoms by walking ``modules.content.atoms``
and **skips every ``_``-prefixed module**. So the split is already mechanical and
enforced: a non-underscore module with ``ATOM_META`` is a *node*; a
``_``-prefixed module (``atoms/_qa_rail_common.py``) is a *library*. This lint
reuses that contract:

  ALLOWED   from modules.content.atoms._qa_rail_common import reviewer_to_dict
  ALLOWED   from modules.content.multi_model_qa import MultiModelQA    (module-root lib)
  ALLOWED   from services.image_service import get_image_service       (substrate)
  VIOLATION from modules.content.stages.generate_content import GenerateContentStage
  VIOLATION from modules.content.atoms import two_pass_writer          (sibling node)

Scan set
--------
- Every ``*.py`` under ``modules/content/atoms/`` (the node package), checked for
  BOTH rules.
- Plus each module-root library an atom imports (``modules.content.<name>`` —
  e.g. ``multi_model_qa``), checked for the STAGE rule only. This catches an atom
  reaching a stage *through* a thin library seam, without pulling in the
  module's substrate-facing boundary (``modules/content/api.py``), which no atom
  imports.

Baseline ratchet
----------------
Existing violations are captured per-file in ``atom_independence_baseline.json``;
the lint fails only when a file exceeds its baseline (a NEW atom→stage /
atom→atom import) or a previously-clean file grows one. The baseline may only
shrink — lower a file's number when you move the code into a library and re-run
with ``--update-baseline``.

Escape hatch: an ``atom-independence-ok`` marker in the offending import's line
span exempts a genuinely-intentional case. Prefer the bare form
``# atom-independence-ok: <reason>`` (mirrors ``silent-ok``; avoids ruff's
invalid-noqa-directive warning).

Run:
    python scripts/ci/atom_independence_lint.py                    # check
    python scripts/ci/atom_independence_lint.py --update-baseline  # re-baseline

Exit 0 = no new violations, exit 1 = at least one new violation.
"""
from __future__ import annotations

import argparse
import ast
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from lib_scan_floor import require_scanned  # noqa: E402

REPO_ROOT = Path(__file__).resolve().parents[2]
BASELINE_PATH = Path(__file__).resolve().parent / "atom_independence_baseline.json"

CONTENT_DIR = REPO_ROOT / "src" / "cofounder_agent" / "modules" / "content"
ATOMS_DIR = CONTENT_DIR / "atoms"

STAGE_PREFIX = "modules.content.stages"
ATOM_PKG = "modules.content.atoms"
CONTENT_PREFIX = "modules.content."

OVERRIDE_MARKER = "atom-independence-ok"


def _is_stage_module(module: str) -> bool:
    return module == STAGE_PREFIX or module.startswith(STAGE_PREFIX + ".")


def _sibling_atom_segment(module: str) -> str | None:
    """For ``modules.content.atoms.<seg>[...]``, return ``<seg>`` (the sibling
    module) when it is a *node* (not ``_``-prefixed); else None. The bare package
    ``modules.content.atoms`` itself returns None (handled via its import names).
    """
    if not module.startswith(ATOM_PKG + "."):
        return None
    seg = module[len(ATOM_PKG) + 1 :].split(".")[0]
    if seg and not seg.startswith("_"):
        return seg
    return None


def _node_has_override(node: ast.AST, lines: list[str]) -> bool:
    """True if an ``atom-independence-ok`` marker appears anywhere in the node's
    line span."""
    start = getattr(node, "lineno", 1)
    end = start
    for child in ast.walk(node):
        node_end = getattr(child, "end_lineno", None)
        if node_end is not None and node_end > end:
            end = node_end
    for ln in range(start, end + 1):  # AST linenos 1-indexed, list 0-indexed
        if 0 <= ln - 1 < len(lines) and OVERRIDE_MARKER in lines[ln - 1]:
            return True
    return False


def scan_source(source: str, *, check_sibling_atoms: bool = True) -> list[tuple[int, str]]:
    """Return ``(lineno, reason)`` for each un-overridden violation.

    ``check_sibling_atoms`` gates the sibling-atom rule: True for atom (node)
    files, False for module-root library files (which are scanned for the stage
    rule only — a library is not itself a graph node).
    """
    try:
        tree = ast.parse(source)
    except (SyntaxError, ValueError):
        return []
    lines = source.splitlines()
    out: list[tuple[int, str]] = []

    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                name = alias.name
                if _is_stage_module(name):
                    if not _node_has_override(node, lines):
                        out.append((node.lineno, f"imports stage module {name}"))
                elif check_sibling_atoms:
                    seg = _sibling_atom_segment(name)
                    if seg and not _node_has_override(node, lines):
                        out.append((node.lineno, f"imports sibling atom {seg}"))
        elif isinstance(node, ast.ImportFrom) and node.module:
            module = node.module
            if _is_stage_module(module):
                if not _node_has_override(node, lines):
                    out.append((node.lineno, f"imports stage module {module}"))
            elif check_sibling_atoms:
                if module == ATOM_PKG:
                    # ``from modules.content.atoms import two_pass_writer`` —
                    # flag each imported name that is a node (non-underscore).
                    for alias in node.names:
                        if not alias.name.startswith("_") and not _node_has_override(node, lines):
                            out.append((node.lineno, f"imports sibling atom {alias.name}"))
                else:
                    seg = _sibling_atom_segment(module)
                    if seg and not _node_has_override(node, lines):
                        out.append((node.lineno, f"imports sibling atom {seg}"))
    return out


def _atom_files() -> list[Path]:
    if not ATOMS_DIR.exists():
        return []
    return [p for p in sorted(ATOMS_DIR.rglob("*.py")) if p.name != "__init__.py"]


def _atom_imported_local_libs() -> set[Path]:
    """Module-root ``modules/content/<name>.py`` files imported by some atom.

    These are the library seams an atom may legitimately import (e.g.
    ``multi_model_qa``); scanning them for the stage rule catches an atom reaching
    a stage *through* the seam. ``atoms`` / ``stages`` subpackages and the
    substrate-facing ``api`` boundary (which no atom imports) are excluded.
    """
    libs: set[Path] = set()
    for f in _atom_files():
        try:
            tree = ast.parse(f.read_text(encoding="utf-8"))
        except (SyntaxError, ValueError, OSError):
            continue
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom) and node.module and node.module.startswith(CONTENT_PREFIX):
                rest = node.module[len(CONTENT_PREFIX) :]
                if "." not in rest and rest not in ("atoms", "stages"):
                    cand = CONTENT_DIR / f"{rest}.py"
                    if cand.exists():
                        libs.add(cand)
    return libs


def _rel(path: Path) -> str:
    return str(path.relative_to(REPO_ROOT)).replace("\\", "/")


_SCANNED = 0  # files actually read by the last compute_counts() call


def compute_counts() -> dict[str, int]:
    """Map of ``relpath -> violation count`` across the atom package and the
    module-root library seams atoms import."""
    global _SCANNED
    _SCANNED = 0
    counts: dict[str, int] = {}
    for f in _atom_files():
        _SCANNED += 1
        n = len(scan_source(f.read_text(encoding="utf-8"), check_sibling_atoms=True))
        if n:
            counts[_rel(f)] = n
    for f in _atom_imported_local_libs():
        _SCANNED += 1
        n = len(scan_source(f.read_text(encoding="utf-8"), check_sibling_atoms=False))
        if n:
            counts[_rel(f)] = n
    return counts


def load_baseline() -> dict[str, int]:
    if not BASELINE_PATH.exists():
        return {}
    return json.loads(BASELINE_PATH.read_text(encoding="utf-8"))


def _violations_for(rel: str, *, check_sibling_atoms: bool) -> list[tuple[int, str]]:
    return scan_source(
        (REPO_ROOT / rel).read_text(encoding="utf-8"),
        check_sibling_atoms=check_sibling_atoms,
    )


def main() -> int:
    parser = argparse.ArgumentParser(description="Atom-independence lint.")
    parser.add_argument(
        "--update-baseline",
        action="store_true",
        help="Regenerate atom_independence_baseline.json from the current tree.",
    )
    args = parser.parse_args()

    counts = compute_counts()

    if args.update_baseline:
        BASELINE_PATH.write_text(
            json.dumps(dict(sorted(counts.items())), indent=2) + "\n",
            encoding="utf-8",
        )
        total = sum(counts.values())
        print(
            f"atom_independence_lint: baseline written - "
            f"{len(counts)} files, {total} atom->stage/atom import(s) grandfathered."
        )
        return 0

    baseline = load_baseline()
    lib_rels = {_rel(f) for f in _atom_imported_local_libs()}
    regressions: list[str] = []
    for rel, n in sorted(counts.items()):
        allowed = baseline.get(rel, 0)
        if n > allowed:
            regressions.append(f"  {rel}: {n} violation(s), baseline allows {allowed}")
            for lineno, reason in _violations_for(rel, check_sibling_atoms=rel not in lib_rels):
                regressions.append(f"      L{lineno}: {reason}")

    if regressions:
        print("NEW ATOM-INDEPENDENCE VIOLATION (not in baseline):")
        for r in regressions:
            print(r)
        print(
            "\nAn atom is an independent graph node: it may import utilities/"
            "libraries (an `atoms/_helper`, a module-root lib, or `services.*`) "
            "but NOT a stage or a sibling atom - else an architect LLM composing "
            "it into a graph silently runs code the graph_def can't see. Move the "
            "shared logic into a library (an `atoms/_helper.py`) and import that. "
            "If this is genuinely intentional, add `# atom-independence-ok: "
            "<reason>`. If you removed a violation, re-run with --update-baseline."
        )
        return 1

    require_scanned(_SCANNED, lint="atom_independence_lint", roots=[ATOMS_DIR])
    total = sum(counts.values())
    print(
        f"atom_independence_lint: clean - no new atom->stage/atom imports "
        f"({total} baselined across {len(counts)} files, {_SCANNED} scanned; "
        "ratchet only shrinks)."
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
