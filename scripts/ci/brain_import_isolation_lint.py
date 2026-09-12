#!/usr/bin/env python3
"""CI lint: the brain daemon must not import worker code at module scope.

The brain (``poindexter/brain/``) ships as its own container image, which
copies ``poindexter/__init__.py`` and ``poindexter/brain/`` and nothing else
(``poindexter/brain/Dockerfile``). Anything it imports from
``poindexter.services`` / ``plugins`` / ``modules`` / ``utils`` / ``routes`` /
``schemas`` / ``config`` / ``tasks`` therefore does not exist where the brain
runs. Such an import either crashes the daemon at start or -- worse -- sits
behind a ``try/except`` that leaves a feature silently inert in production
while every unit test (run from the full worktree) passes.

Earned 2026-09-12: the operator-URL probe imported the settings registry to
learn which ``*_url`` keys another probe owns, wrapped in a fallback to ``{}``.
The image lacked the module, the fallback took, and the rule shipped as a
no-op behind green CI (stack #3673). The seam the brain may use for that
knowledge is the database (``app_settings.owner``), which the worker's seeder
keeps in step with the registry -- PostgreSQL as spinal cord, not imports.

Rule: no ``import`` / ``from ... import`` of those packages at module scope in
any ``poindexter/brain/*.py``. Function-scope (lazy) imports are tolerated for
the two legacy call sites that degrade explicitly when the worker tree is
absent, but new code should reach the worker through the DB or HTTP instead.
Escape hatch for a deliberate exception: ``# brain-import-ok`` on the line.

Exit 1 with ``path:line: message`` per offence; exit 0 when clean. Fails when
the brain directory is missing or nothing was scanned (see lib_scan_floor).
"""
from __future__ import annotations

import ast
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from lib_scan_floor import require_dir, require_scanned  # noqa: E402

REPO_ROOT = Path(__file__).resolve().parents[2]
BRAIN_DIR = REPO_ROOT / "src" / "cofounder_agent" / "poindexter" / "brain"
FORBIDDEN_ROOTS = (
    "poindexter.services",
    "poindexter.plugins",
    "poindexter.modules",
    "poindexter.utils",
    "poindexter.routes",
    "poindexter.schemas",
    "poindexter.config",
    "poindexter.tasks",
)
ESCAPE = "# brain-import-ok"


def _is_forbidden(module: str | None) -> bool:
    if not module:
        return False
    return any(module == root or module.startswith(root + ".") for root in FORBIDDEN_ROOTS)


def scan_source(source: str, rel: str) -> list[tuple[int, str]]:
    """Return ``(lineno, message)`` for every module-scope import of worker code."""
    tree = ast.parse(source, filename=rel)
    lines = source.splitlines()
    out: list[tuple[int, str]] = []
    # Module scope = statements directly in the module body, including those
    # nested in module-level try/if blocks (a try/except at module scope is the
    # exact shape that hides the failure).
    stack: list[ast.AST] = list(tree.body)
    while stack:
        node = stack.pop()
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef, ast.Lambda)):
            continue
        names: list[str] = []
        if isinstance(node, ast.Import):
            names = [a.name for a in node.names]
        elif isinstance(node, ast.ImportFrom) and node.level == 0:
            names = [node.module or ""]
        for name in names:
            if _is_forbidden(name):
                line = lines[node.lineno - 1] if node.lineno - 1 < len(lines) else ""
                if ESCAPE in line:
                    continue
                out.append(
                    (
                        node.lineno,
                        f"module-scope import of {name!r}: the brain image does not ship it "
                        "(read the DB or call HTTP instead; lazy function-scope import only "
                        "with an explicit degrade path)",
                    )
                )
        for child in ast.iter_child_nodes(node):
            if isinstance(child, ast.stmt) or isinstance(child, ast.excepthandler):
                stack.append(child)
    return sorted(out)  # stack order is LIFO; report in file order


def main() -> int:
    require_dir(BRAIN_DIR, lint="brain_import_isolation_lint")
    scanned = 0
    offences: list[str] = []
    for path in sorted(BRAIN_DIR.rglob("*.py")):
        rel = path.relative_to(REPO_ROOT).as_posix()
        scanned += 1
        for lineno, msg in scan_source(path.read_text(encoding="utf-8"), rel):
            offences.append(f"{rel}:{lineno}: {msg}")
    require_scanned(scanned, lint="brain_import_isolation_lint", roots=(BRAIN_DIR,))
    if offences:
        print("\n".join(offences))
        print(
            f"\nbrain_import_isolation_lint: {len(offences)} module-scope worker import(s) "
            f"in {scanned} brain files. The brain container copies only poindexter/brain/."
        )
        return 1
    print(f"brain_import_isolation_lint: OK — no module-scope worker imports ({scanned} files).")
    return 0


if __name__ == "__main__":
    sys.exit(main())
