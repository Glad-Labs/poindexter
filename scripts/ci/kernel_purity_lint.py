#!/usr/bin/env python3
"""CI lint: kernel (services/ + plugins/) must not import modules.* directly.

Seam 2 (poindexter#666) — kernel-purity guard. The kernel substrate
(services/ + plugins/) must not reach into business-module internals. The
correct direction is module→kernel (content uses the platform), never
kernel→module.

The lazy imports baselined below are real Direction-B (kernel→module)
violations that need a follow-up "module public-service interface" refactor
before they can be removed (#666). They live inside function bodies (lazy
imports) so they are load-time safe and do not cause circular-import crashes.

Any violation NOT covered by the baseline fails CI immediately. The baseline
shrinks to zero as each lazy import is refactored to go through the module's
public surface.

Baseline key: (file, import target) — NOT line number
-----------------------------------------------------
Each entry is ``"relative/path.py::imported.module.target" -> count``. A key
is stable under line drift, so an unrelated PR that adds lines *above* a
baselined import no longer fails CI on a violation it never touched
(poindexter#929: the old line-keyed baseline was re-baselined 16 times across
6 files — ``publish_service.py`` alone went 1038 → 1082 → 1095 → 1154 → 1181
→ 1218 → 1221 → 1251, every hop collateral damage from an unrelated change).

The ratchet still fails on:
  - a NEW kernel→module import in a previously-clean file (key absent, so the
    allowance is 0);
  - a new import of a DIFFERENT module target in an already-baselined file
    (different key);
  - an ADDITIONAL import of an already-baselined target in the same file
    (count exceeded).

Accepted residual gap: a file may swap one baselined import site for a
different one at the same target and keep its count. That is a strictly
smaller hole than line-keying, and it costs nothing on unrelated PRs.

Every entry must carry a rationale comment on the line above it, and that is
enforced mechanically (see ``parse_baseline_source``) rather than left
to good intentions. There is deliberately no ``--update-baseline`` flag:
regenerating the dict would delete those comments. Shrink it by hand.

Run: python scripts/ci/kernel_purity_lint.py
Exit 0 = no new violations (baseline-only allowed), exit 1 = new violation found.
"""
from __future__ import annotations

import ast
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2] / "src" / "cofounder_agent"
SCAN_DIRS = [
    ROOT / "services",
    ROOT / "plugins",
]

# ---------------------------------------------------------------------------
# Baselined violations — lazy imports that are load-time safe but are
# genuine Direction-B (kernel→module) violations. Each MUST carry a comment
# explaining WHY it is baselined (enforced, not honour-system). Shrink this
# map as the module public interface is built out (poindexter#666 follow-up).
#
# Format: "relative/path/from/ROOT::imported.module.target" -> allowed count
# ---------------------------------------------------------------------------
KERNEL_PURITY_BASELINE: dict[str, int] = {
    # deepeval_rails calls content_validator lazily to avoid a circular
    # import; needs a platform.validate seam.
    "services/deepeval_rails.py::modules.content.api": 1,
    # guardrails_rails calls content_validator lazily for the same reason.
    "services/guardrails_rails.py::modules.content.api": 1,
    # pipeline_templates' dev_diary factory imports the narrate_bundle atom
    # lazily; dev_diary is the one remaining non-graph_def template path.
    "services/pipeline_templates/__init__.py::modules.content.api": 1,
    # post_pipeline_actions reaches modules.content.api (the module's public
    # surface) lazily from three sites: the auto-publish gate evaluation, the
    # auto-publish task itself, and MultiModelQA.
    "services/post_pipeline_actions.py::modules.content.api": 3,
    # publish_service calls record_post_approve_metrics lazily via the
    # modules/content/api public seam — same pattern as post_pipeline_actions.
    "services/publish_service.py::modules.content.api": 1,
    # research_context calls internal_link_coherence lazily.
    "services/research_context.py::modules.content.api": 1,
    # social_drafts.reconcile_missing_drafts calls modules.content.api lazily:
    # the atom it re-invokes imports SocialDraftsService at module level, so a
    # top-level import here would be circular (poindexter#863).
    "services/social_drafts.py::modules.content.api": 1,
    # topic_proposal_service calls build_topic_decision_artifact lazily (moved
    # off the top level in poindexter#666, which was the Direction-B violation
    # warranting the immediate fix); the remaining lazy usage is baselined
    # pending a gate-artifact public seam on the content module.
    "services/topic_proposal_service.py::modules.content.api": 1,
}

# One ``"key": <count>,`` line, as the baseline is written above. The
# rationale-comment check parses this file's own source, so the parse is
# cross-checked against KERNEL_PURITY_BASELINE itself (see
# parse_baseline_source) rather than trusted blind.
_BASELINE_ENTRY_RE = re.compile(r'^\s*"(?P<key>[^"]+)"\s*:\s*\d+\s*,\s*$')
_BASELINE_OPEN = "KERNEL_PURITY_BASELINE"


def _modules_targets(node: ast.stmt) -> list[str]:
    """Return the ``modules.*`` targets an import node reaches, if any.

    ``from modules.content.api import x`` -> ``["modules.content.api"]``
    ``import modules.content.api``        -> ``["modules.content.api"]``
    Relative imports (``node.module is None``) never match.
    """
    if isinstance(node, ast.ImportFrom):
        if node.module and (node.module == "modules" or node.module.startswith("modules.")):
            return [node.module]
        return []
    if isinstance(node, ast.Import):
        return [
            alias.name
            for alias in node.names
            if alias.name == "modules" or alias.name.startswith("modules.")
        ]
    return []


def scan_source(source: str) -> list[tuple[int, str]]:
    """Return ``(lineno, target)`` for every kernel→module import in ``source``."""
    try:
        tree = ast.parse(source)
    except (SyntaxError, ValueError):
        return []
    out: list[tuple[int, str]] = []
    for node in ast.walk(tree):
        if isinstance(node, (ast.Import, ast.ImportFrom)):
            for target in _modules_targets(node):
                out.append((node.lineno, target))
    return sorted(out)


def _rel(path: Path) -> str:
    return str(path.relative_to(ROOT)).replace("\\", "/")


def scan_tree() -> dict[str, list[int]]:
    """Map ``"relpath::target" -> [lineno, ...]`` across the kernel scan dirs."""
    found: dict[str, list[int]] = {}
    for scan_dir in SCAN_DIRS:
        if not scan_dir.exists():
            continue
        for py_file in sorted(scan_dir.rglob("*.py")):
            try:
                source = py_file.read_text(encoding="utf-8")
            except (OSError, UnicodeDecodeError):
                continue
            rel = _rel(py_file)
            for lineno, target in scan_source(source):
                found.setdefault(f"{rel}::{target}", []).append(lineno)
    return found


def compute_counts() -> dict[str, int]:
    """Map ``"relpath::target" -> violation count`` across the kernel scan dirs."""
    return {key: len(linenos) for key, linenos in scan_tree().items()}


def parse_baseline_source(source: str | None = None) -> list[tuple[str, bool]]:
    """Parse the baseline dict out of source: ``[(key, has_rationale), ...]``.

    Reads this file's own source by default. The recovered key set is
    cross-checked against ``KERNEL_PURITY_BASELINE`` by the caller, so a
    formatting change that defeats the parse fails loudly rather than
    vacuously passing the rationale check.
    """
    if source is None:
        source = Path(__file__).read_text(encoding="utf-8")
    lines = source.splitlines()

    inside = False
    entries: list[tuple[str, bool]] = []
    for idx, line in enumerate(lines):
        if not inside:
            if line.startswith(_BASELINE_OPEN) and line.rstrip().endswith("{"):
                inside = True
            continue
        if line.startswith("}"):
            break
        match = _BASELINE_ENTRY_RE.match(line)
        if not match:
            continue
        # Walk up past blank lines; the first line with content must be a
        # comment. Another entry directly above does NOT count as cover — one
        # rationale per entry.
        prev = idx - 1
        while prev >= 0 and not lines[prev].strip():
            prev -= 1
        documented = prev >= 0 and lines[prev].lstrip().startswith("#")
        entries.append((match.group("key"), documented))
    return entries


def main() -> int:
    found = scan_tree()

    regressions: list[str] = []
    for key in sorted(found):
        linenos = found[key]
        allowed = KERNEL_PURITY_BASELINE.get(key, 0)
        if len(linenos) > allowed:
            rel, target = key.split("::", 1)
            lines_str = ", ".join(f"L{n}" for n in linenos)
            regressions.append(
                f"  {rel}: {len(linenos)} import(s) of {target}, "
                f"baseline allows {allowed} — at {lines_str}"
            )

    if regressions:
        print("KERNEL PURITY VIOLATIONS (new — not covered by baseline):")
        for r in regressions:
            print(r)
        print(
            f"\n{len(regressions)} new violation(s) found. The kernel "
            "(services/ + plugins/) must not import modules.* — route through "
            "the module's public surface (modules/content/api.py) instead. "
            "If this genuinely cannot be avoided yet, add/raise the "
            "'path::target' entry in KERNEL_PURITY_BASELINE *with a comment "
            "explaining why*. See poindexter#666."
        )
        return 1

    # Self-check: every baseline entry must carry a rationale comment. Verify
    # the source parse actually saw the live dict before trusting its verdict.
    parsed = parse_baseline_source()
    if sorted(key for key, _ in parsed) != sorted(KERNEL_PURITY_BASELINE):
        print(
            "kernel_purity_lint: cannot parse KERNEL_PURITY_BASELINE from source "
            f"(parsed {len(parsed)} entries, dict holds {len(KERNEL_PURITY_BASELINE)}). "
            'Keep one `"path::target": <count>,` per line so the '
            "rationale-comment check stays enforceable."
        )
        return 1
    undocumented = [key for key, documented in parsed if not documented]
    if undocumented:
        print("KERNEL PURITY BASELINE ENTRIES MISSING A RATIONALE COMMENT:")
        for key in undocumented:
            print(f"  {key}")
        print(
            "\nEvery baselined kernel→module import must say WHY it is "
            "baselined — put a `# <reason>` comment on the line above it."
        )
        return 1

    total_files = sum(len(list(d.rglob("*.py"))) for d in SCAN_DIRS if d.exists())
    baselined_found = sum(len(found.get(key, [])) for key in KERNEL_PURITY_BASELINE)
    print(
        f"kernel_purity_lint: clean — {total_files} files checked, "
        f"0 new violations "
        f"({baselined_found}/{sum(KERNEL_PURITY_BASELINE.values())} "
        "baselined lazy imports still present)"
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
