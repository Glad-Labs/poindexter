#!/usr/bin/env python3
"""CI lint: the brain daemon must not import worker code, at any scope.

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

Rule: no ``import`` / ``from ... import`` of those packages anywhere in any
``poindexter/brain/*.py`` -- module scope, class body or function body. A lazy
import inside a function is no safer than one at the top of the file: it fails
in the image just the same, only later. Function scope was tolerated wholesale
until 2026-09-28, and one of the sites it let through was ``brain_daemon.main()``
building an ``AppContainer`` that nothing ever read. The import failed on every
boot for four months, logging a warning that probes depending on the container
would fail; no probe did. New code reaches the worker through the DB or HTTP.

``TOLERATED_LAZY_IMPORTS`` held the function-scope sites that predate the
all-scopes rule. The last one was retired 2026-09-28
(Glad-Labs/poindexter#1095): ``alert_dispatcher._resolve_notify_fn`` tried the
worker's ``notify_operator`` before the brain's own ``notify``, so in the image
it failed on every call while tests run from the full tree resolved it. The
list is empty and only shrinks: nothing is added to it, and an entry whose
function no longer imports worker code fails the lint until it is deleted.
Escape hatch for a deliberate exception: ``# brain-import-ok: <why>`` on the
import's first line.

Exit 1 with ``path:line: message`` per offence; exit 0 when clean. Fails when
the brain directory is missing or nothing was scanned (see lib_scan_floor).
"""
from __future__ import annotations

import ast
import sys
from collections.abc import Iterable, Mapping
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

# (path relative to the brain dir, enclosing function's qualname) -> why it is
# still here. Shrink-only, and empty since 2026-09-28; see the module docstring.
TOLERATED_LAZY_IMPORTS: dict[tuple[str, str], str] = {}


def _is_forbidden(module: str | None) -> bool:
    if not module:
        return False
    return any(module == root or module.startswith(root + ".") for root in FORBIDDEN_ROOTS)


def find_worker_imports(source: str, rel: str) -> list[tuple[int, str, str | None]]:
    """Every import of worker code in ``source`` as ``(lineno, module, function)``.

    ``function`` is the enclosing function's dotted qualname (``outer.inner``,
    ``Class.method``), or ``None`` at module scope and in a class body -- both
    run at import time.
    """
    tree = ast.parse(source, filename=rel)
    found: list[tuple[int, str, str | None]] = []

    def visit(node: ast.AST, scope: tuple[str, ...], function: str | None) -> None:
        for child in ast.iter_child_nodes(node):
            if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef)):
                inner = (*scope, child.name)
                visit(child, inner, ".".join(inner))
                continue
            if isinstance(child, ast.ClassDef):
                visit(child, (*scope, child.name), function)
                continue
            names: list[str] = []
            if isinstance(child, ast.Import):
                names = [a.name for a in child.names]
            elif isinstance(child, ast.ImportFrom) and child.level == 0:
                names = [child.module or ""]
            found.extend((child.lineno, name, function) for name in names if _is_forbidden(name))
            visit(child, scope, function)

    visit(tree, (), None)
    return sorted(found, key=lambda item: item[0])


def _offences(
    found: Iterable[tuple[int, str, str | None]],
    lines: list[str],
    tolerated: frozenset[str],
) -> list[tuple[int, str]]:
    out: list[tuple[int, str]] = []
    for lineno, name, function in found:
        line = lines[lineno - 1] if lineno - 1 < len(lines) else ""
        if ESCAPE in line or (function is not None and function in tolerated):
            continue
        where = "at module scope" if function is None else f"in {function}()"
        out.append(
            (
                lineno,
                f"import of {name!r} {where}: the brain image does not ship it, so this "
                "fails there even when it is lazy (read the DB or call HTTP instead)",
            )
        )
    return out


def scan_source(source: str, rel: str, tolerated: frozenset[str] = frozenset()) -> list[tuple[int, str]]:
    """Return ``(lineno, message)`` for every worker import not excused.

    ``tolerated`` holds the qualnames of this file's ``TOLERATED_LAZY_IMPORTS``
    functions; an escape-marked line is excused anywhere.
    """
    return _offences(find_worker_imports(source, rel), source.splitlines(), tolerated)


def check_tree(
    brain_dir: Path,
    repo_root: Path,
    tolerated_sites: Mapping[tuple[str, str], str] = TOLERATED_LAZY_IMPORTS,
) -> tuple[int, list[str]]:
    """Scan every ``*.py`` under ``brain_dir``; return ``(files scanned, offences)``.

    Besides unexcused imports, an offence is a tolerated site whose function no
    longer imports worker code (or no longer exists), so the list cannot outlive
    the debt it records.
    """
    scanned = 0
    offences: list[str] = []
    live: set[tuple[str, str]] = set()
    for path in sorted(brain_dir.rglob("*.py")):
        rel = path.relative_to(repo_root).as_posix()
        key = path.relative_to(brain_dir).as_posix()
        source = path.read_text(encoding="utf-8")
        scanned += 1
        tolerated = frozenset(function for (file, function) in tolerated_sites if file == key)
        found = find_worker_imports(source, rel)
        live.update((key, function) for _, _, function in found if function in tolerated)
        offences.extend(f"{rel}:{ln}: {msg}" for ln, msg in _offences(found, source.splitlines(), tolerated))
    lint_rel = Path(__file__).resolve().relative_to(REPO_ROOT).as_posix()
    for file, function in sorted(set(tolerated_sites) - live):
        offences.append(
            f"{lint_rel}: TOLERATED_LAZY_IMPORTS entry ({file!r}, {function!r}) is stale -- "
            "that function no longer imports worker code; delete the entry"
        )
    return scanned, offences


def main() -> int:
    require_dir(BRAIN_DIR, lint="brain_import_isolation_lint")
    scanned, offences = check_tree(BRAIN_DIR, REPO_ROOT)
    require_scanned(scanned, lint="brain_import_isolation_lint", roots=(BRAIN_DIR,))
    if offences:
        print("\n".join(offences))
        print(
            f"\nbrain_import_isolation_lint: {len(offences)} problem(s) in {scanned} brain "
            "files. The brain container copies only poindexter/brain/."
        )
        return 1
    print(
        f"brain_import_isolation_lint: OK — no worker imports ({scanned} files, "
        f"{len(TOLERATED_LAZY_IMPORTS)} tolerated legacy site(s))."
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
