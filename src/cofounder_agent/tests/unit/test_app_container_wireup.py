"""Unit tests for SiteConfig DI migration PR 2 — entry-point wireup.

Design doc: ``docs/architecture/2026-05-28-site-config-di-migration.md``.

The migration's PR 2 wires ``services.bootstrap.build_container(pool)``
into every entry point (FastAPI worker lifespan, brain daemon main,
Prefect flow subprocess, CLI ``_impl()`` bodies). Each entry-point test
here pins ONE wiring seam at the source-AST level — the same approach
the scheduled_publisher lifespan regression uses, for the same reason:
the full app graph is too heavy to import in a unit test, but the
shape of the call site is exactly what the migration's correctness
depends on.

Future PRs in the migration retire ``wire_site_config_modules``; when
that happens these tests should be updated to assert the container
construction call still lives at the right scope and that the
removed-wiring isn't accidentally re-introduced.
"""

from __future__ import annotations

import ast
import inspect
from pathlib import Path

import pytest

# Project root — walk up until we find ``main.py`` so this works whether
# pytest is invoked from the repo root or from ``src/cofounder_agent``.
_HERE = Path(__file__).resolve()
for _p in _HERE.parents:
    if (_p / "main.py").is_file():
        _APP_ROOT = _p
        break
else:  # pragma: no cover — repo invariant
    raise RuntimeError("main.py not found walking up from test_app_container_wireup.py")

_BRAIN_DAEMON = None
for _p in _HERE.parents:
    if (_p / "brain" / "brain_daemon.py").is_file():
        _BRAIN_DAEMON = _p / "brain" / "brain_daemon.py"
        break


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
        assert "from services.bootstrap import build_container" in source, (
            "main.py must import build_container from services.bootstrap"
        )


# ---------------------------------------------------------------------------
# brain/brain_daemon.py main() wires module-global _APP_CONTAINER
# ---------------------------------------------------------------------------


@pytest.mark.skipif(_BRAIN_DAEMON is None, reason="brain_daemon.py not on disk")
class TestBrainDaemonWiresContainer:
    """``brain.brain_daemon.main()`` must build the container after the
    pool is alive and stash it on the module-global ``_APP_CONTAINER``,
    reachable via ``get_app_container()``."""

    def test_main_assigns_app_container_global(self):
        tree = _source_tree(_BRAIN_DAEMON)
        main_fn = _find_function(tree, "main")
        assert main_fn is not None, "brain_daemon.py is missing main()"

        # Look for ``_APP_CONTAINER = await _build_app_container(pool)``
        # inside main. The variable is a module-level global declared by
        # an explicit ``global _APP_CONTAINER`` statement first.
        global_decls = [
            n for n in ast.walk(main_fn)
            if isinstance(n, ast.Global) and "_APP_CONTAINER" in n.names
        ]
        assert global_decls, (
            "brain_daemon.main() must declare ``global _APP_CONTAINER`` "
            "before assigning it (SiteConfig DI migration PR 2)"
        )

        assigns = [
            n for n in ast.walk(main_fn)
            if isinstance(n, ast.Assign)
            and len(n.targets) == 1
            and isinstance(n.targets[0], ast.Name)
            and n.targets[0].id == "_APP_CONTAINER"
        ]
        # We expect at least one assignment of _APP_CONTAINER to an
        # ``await`` expression (the build call). The early ``None``
        # fallback assignment is fine too — we only care that one of
        # them is the await call.
        await_assigns = [
            a for a in assigns
            if isinstance(a.value, ast.Await)
        ]
        assert await_assigns, (
            "brain_daemon.main() must assign ``_APP_CONTAINER = "
            "await build_container(pool)`` (SiteConfig DI migration PR 2)"
        )

    def test_get_app_container_helper_exists(self):
        """The module exposes ``get_app_container()`` returning the
        module-global handle, mirror of ``get_oauth_client``."""
        source = _BRAIN_DAEMON.read_text(encoding="utf-8")
        assert "def get_app_container():" in source, (
            "brain_daemon.py must expose get_app_container() so probes "
            "can reach the container without importing the module global"
        )
