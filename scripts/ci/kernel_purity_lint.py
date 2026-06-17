#!/usr/bin/env python3
"""CI lint: kernel (services/ + plugins/) must not import modules.* directly.

Seam 2 (poindexter#666) — kernel-purity guard. The kernel substrate
(services/ + plugins/) must not reach into business-module internals. The
correct direction is module→kernel (content uses the platform), never
kernel→module.

The 8 lazy violations below are BASELINED — they are real Direction-B
(kernel→module) violations that need a follow-up "module public-service
interface" refactor before they can be removed (#666). They live inside
function bodies (lazy imports) so they are load-time safe and do not cause
circular-import crashes. They are NOT new debt introduced by this PR.

Any violation NOT in the baseline fails CI immediately. The baseline shrinks
to zero as each lazy import is refactored to go through the module's public
surface.

Run: python scripts/ci/kernel_purity_lint.py
Exit 0 = no new violations (baseline-only allowed), exit 1 = new violation found.
"""
import sys
import ast
from pathlib import Path

ROOT = Path(__file__).parents[2] / "src" / "cofounder_agent"
SCAN_DIRS = [
    ROOT / "services",
    ROOT / "plugins",
]

# ---------------------------------------------------------------------------
# Baselined violations — lazy imports that are load-time safe but are
# genuine Direction-B (kernel→module) violations. Each must carry a comment
# explaining WHY it is baselined. Shrink this list as the module public
# interface is built out (poindexter#666 follow-up).
#
# Format: "relative/path/from/ROOT:lineno"
# ---------------------------------------------------------------------------
KERNEL_PURITY_BASELINE = {
    # deepeval_rails calls content_validator lazily to avoid circular import;
    # needs a platform.validate seam.
    "services/deepeval_rails.py:164",
    # guardrails_rails calls content_validator lazily for the same reason.
    "services/guardrails_rails.py:90",
    # pipeline_templates dev_diary factory imports narrate_bundle atom lazily;
    # dev_diary template is the one remaining non-graph_def path.
    "services/pipeline_templates/__init__.py:106",
    # post_pipeline_actions calls auto_publish_gate lazily (3 import sites).
    "services/post_pipeline_actions.py:396",
    "services/post_pipeline_actions.py:446",
    "services/post_pipeline_actions.py:522",
    # publish_service calls record_post_approve_metrics lazily (via
    # modules/content/api public seam — same pattern as post_pipeline_actions).
    # Line shifted 1038 -> 1082 by the #729 _niche_allowlist_block helper.
    "services/publish_service.py:1082",
    # research_context calls internal_link_coherence lazily.
    "services/research_context.py:146",
    # topic_proposal_service calls build_topic_decision_artifact lazily
    # (moved from top-level to lazy in poindexter#666 — the top-level
    # import was the Direction-B violation that warranted the immediate fix;
    # the remaining lazy usage here is baselined pending a gate-artifact
    # public seam on the content module).
    "services/topic_proposal_service.py:405",
}


def _is_modules_import(node: ast.stmt) -> bool:
    """Return True if the AST node imports from the modules.* namespace."""
    if isinstance(node, ast.ImportFrom):
        return bool(node.module and (
            node.module == "modules"
            or node.module.startswith("modules.")
        ))
    if isinstance(node, ast.Import):
        return any(
            alias.name == "modules" or alias.name.startswith("modules.")
            for alias in node.names
        )
    return False


violations = []
baselined_found = []

for scan_dir in SCAN_DIRS:
    if not scan_dir.exists():
        continue
    for py_file in sorted(scan_dir.rglob("*.py")):
        try:
            source = py_file.read_text(encoding="utf-8")
            tree = ast.parse(source)
        except (SyntaxError, UnicodeDecodeError):
            continue

        for node in ast.walk(tree):
            if not isinstance(node, (ast.Import, ast.ImportFrom)):
                continue
            if not _is_modules_import(node):
                continue

            rel = str(py_file.relative_to(ROOT)).replace("\\", "/")
            key = f"{rel}:{node.lineno}"
            if key in KERNEL_PURITY_BASELINE:
                baselined_found.append(key)
            else:
                violations.append(
                    f"  {rel}:{node.lineno}: "
                    f"kernel imports modules.* — use module public surface instead"
                )

if violations:
    print("KERNEL PURITY VIOLATIONS (new — not in baseline):")
    for v in violations:
        print(v)
    print(
        f"\n{len(violations)} new violation(s) found. "
        "Add lazy imports inside functions or route through the module's "
        "public service interface. See poindexter#666."
    )
    sys.exit(1)

total_files = sum(
    len(list(d.rglob("*.py"))) for d in SCAN_DIRS if d.exists()
)
print(
    f"kernel_purity_lint: clean — {total_files} files checked, "
    f"0 new violations "
    f"({len(baselined_found)}/{len(KERNEL_PURITY_BASELINE)} baselined lazy imports still present)"
)
sys.exit(0)
