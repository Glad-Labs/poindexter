#!/usr/bin/env python3
"""CI lint: modules/content/ may not import kernel service internals directly.

Seam 1 (poindexter#667 Wave 4) — module-purity guard. Content imports the
Platform *type* (allowed) and capability-scoped handle; it may NOT import:
  - services.site_config  (use platform.config)
  - services.audit_log    (use platform.audit)
  - services.llm_providers.dispatcher.dispatch_complete  (use platform.dispatch)

Run: python scripts/ci/module_purity_lint.py
Exit 0 = clean, exit 1 = violations found (with file+line listed).
"""
import ast
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from lib_scan_floor import require_dir, require_scanned  # noqa: E402

ROOT = Path(__file__).parents[2] / "src" / "cofounder_agent"
CONTENT_DIR = ROOT / "modules" / "content"

# modules/content/ arrived as a physical code move and could move again; a
# vanished CONTENT_DIR used to rglob to nothing and print "clean (0 files
# checked)" with exit 0. Fail instead.
require_dir(CONTENT_DIR, lint="module_purity_lint")

BANNED = [
    ("services.site_config", "use platform.config instead"),
    ("services.audit_log", "use platform.audit instead"),
    ("services.llm_providers.dispatcher", "dispatch_complete — use platform.dispatch.complete instead"),
]

violations = []
scanned = 0
for py_file in sorted(CONTENT_DIR.rglob("*.py")):
    scanned += 1
    try:
        tree = ast.parse(py_file.read_text(encoding="utf-8"))
    except SyntaxError:
        continue
    for node in ast.walk(tree):
        if isinstance(node, (ast.Import, ast.ImportFrom)):
            if isinstance(node, ast.ImportFrom) and node.module:
                module = node.module
                for banned_mod, hint in BANNED:
                    if module == banned_mod or module.startswith(banned_mod + "."):
                        # Check if any of the names imported are the banned ones
                        names = [a.name for a in node.names]
                        # For dispatcher, only flag if dispatch_complete is imported
                        if "dispatcher" in banned_mod:
                            if "dispatch_complete" in names:
                                violations.append(f"{py_file.relative_to(ROOT)}:{node.lineno}: {hint}")
                        else:
                            violations.append(f"{py_file.relative_to(ROOT)}:{node.lineno}: {hint}")

if violations:
    print("MODULE PURITY VIOLATIONS:")
    for v in violations:
        print(f"  {v}")
    sys.exit(1)
else:
    require_scanned(scanned, lint="module_purity_lint", roots=(CONTENT_DIR,))
    print(f"module_purity_lint: clean ({scanned} files checked)")
    sys.exit(0)
