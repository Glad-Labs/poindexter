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
import sys
import ast
from pathlib import Path

ROOT = Path(__file__).parents[2] / "src" / "cofounder_agent"
CONTENT_DIR = ROOT / "modules" / "content"

BANNED = [
    ("services.site_config", "use platform.config instead"),
    ("services.audit_log", "use platform.audit instead"),
    ("services.llm_providers.dispatcher", "dispatch_complete — use platform.dispatch.complete instead"),
]

violations = []
for py_file in sorted(CONTENT_DIR.rglob("*.py")):
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
    print(f"module_purity_lint: clean ({len(list(CONTENT_DIR.rglob('*.py')))} files checked)")
    sys.exit(0)
