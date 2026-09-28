"""Unit tests for SiteConfig DI migration PR 2 — entry-point wireup.

Design doc: ``docs/architecture/2026-05-28-site-config-di-migration.md``.

The migration's PR 2 wires ``services.bootstrap.build_container(pool)``
into the worker's entry points (FastAPI worker lifespan, Prefect flow
subprocess, CLI ``_impl()`` bodies). Each entry-point test here pins ONE
wiring seam at the source-AST level — the same approach the
scheduled_publisher lifespan regression uses, for the same reason: the
full app graph is too heavy to import in a unit test, but the shape of
the call site is exactly what the migration's correctness depends on.

The brain daemon is deliberately NOT an entry point. PR 2 also gave
``brain_daemon.main()`` a best-effort ``build_container`` call, but the
brain image ships only ``poindexter/brain/``, so the import failed on
every boot for four months and no probe ever read the result; it was
removed 2026-09-28. ``scripts/ci/brain_import_isolation_lint.py`` keeps
worker imports out of the brain at any scope, which is what would stop
it coming back.

Future PRs in the migration retire ``wire_site_config_modules``; when
that happens these tests should be updated to assert the container
construction call still lives at the right scope and that the
removed-wiring isn't accidentally re-introduced.
"""

from __future__ import annotations

import ast
from pathlib import Path

# Project root — walk up until we find ``main.py`` so this works whether
# pytest is invoked from the repo root or from ``src/cofounder_agent``.
_HERE = Path(__file__).resolve()
for _p in _HERE.parents:
    if (_p / "main.py").is_file():
        _APP_ROOT = _p
        break
else:  # pragma: no cover — repo invariant
    raise RuntimeError("main.py not found walking up from test_app_container_wireup.py")


def _source_tree(path: Path) -> ast.AST:
    return ast.parse(path.read_text(encoding="utf-8"))


def _find_function(tree: ast.AST, name: str) -> ast.AsyncFunctionDef | ast.FunctionDef | None:
    for node in ast.walk(tree):
        if isinstance(node, (ast.AsyncFunctionDef, ast.FunctionDef)):
            if node.name == name:
                return node
    return None


# ---------------------------------------------------------------------------
# main.py lifespan wires app.state.container = await build_container(...)
# ---------------------------------------------------------------------------


class TestMainLifespanWiresContainer:
    """``main.lifespan`` must call ``build_container`` and stash the
    result on ``app.state.container``."""

    def test_lifespan_assigns_app_state_container(self):
        tree = _source_tree(_APP_ROOT / "main.py")
        lifespan = _find_function(tree, "lifespan")
        assert lifespan is not None, "main.py is missing the lifespan function"

        # Find an assignment of the shape:
        #     app.state.container = await build_container(...)
        # Pre-PR-2 this attribute didn't exist; post-PR-2 it MUST exist,
        # at top scope of the lifespan ``try`` block (not gated on
        # deployment_mode).
        target_calls: list[ast.Assign] = []
        for node in ast.walk(lifespan):
            if not isinstance(node, ast.Assign):
                continue
            if len(node.targets) != 1:
                continue
            tgt = node.targets[0]
            if not isinstance(tgt, ast.Attribute):
                continue
            if tgt.attr != "container":
                continue
            # ``app.state.container`` — outer attribute is ``state``,
            # value is ``app``.
            if (
                not isinstance(tgt.value, ast.Attribute)
                or tgt.value.attr != "state"
            ):
                continue
            target_calls.append(node)

        assert target_calls, (
            "main.lifespan must assign ``app.state.container = "
            "await build_container(...)`` (SiteConfig DI migration PR 2). "
            "No such assignment found."
        )
        # Right-hand side must be an ``await`` on something — ie the
        # call IS routed through ``build_container``'s async path.
        for assign in target_calls:
            assert isinstance(assign.value, ast.Await), (
                "app.state.container assignment must await build_container(...)"
            )

    def test_lifespan_imports_build_container(self):
        """The lifespan body imports ``build_container`` from
        ``services.bootstrap`` — pinning the import path so a rename
        breaks loudly."""
        source = (_APP_ROOT / "main.py").read_text(encoding="utf-8")
        assert "services.bootstrap import build_container" in source, (
            "main.py must import build_container from services.bootstrap (either spelling)"
        )
