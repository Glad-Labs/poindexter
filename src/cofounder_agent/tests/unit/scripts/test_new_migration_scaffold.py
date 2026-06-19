"""Contract tests for the scaffold body emitted by ``scripts/new-migration.py``.

Regression guard for a cryptic-failure foot-gun. The generator used to emit
``up()`` / ``run_migration()`` bodies whose only statement was a SQL *comment*
(``-- TODO: write the SQL``). asyncpg cannot execute a comment-only statement —
it returns no command tag, so its protocol layer raises
``AttributeError: 'NoneType' object has no attribute 'decode'``. When an
unedited scaffold was run through the migration runner (e.g. the
``tests/integration_db`` ``schema_loaded`` fixture replays the whole chain at
session start) it crashed EVERY integration_db test at *setup* with that opaque
error instead of a clear "you haven't written the SQL yet" signal.

The fix makes an unedited scaffold fail LOUDLY and CLEARLY: every generated
entry point (``up`` / ``down`` for the pool interface, ``run_migration`` /
``rollback_migration`` for the conn interface) raises ``NotImplementedError``
naming the migration. These tests pin that contract for both interfaces, and
keep the runner-interface functions present so ``migrations_lint.py`` still
recognises the scaffold.
"""

from __future__ import annotations

import asyncio
import importlib.util
import inspect
from pathlib import Path

import pytest


def _repo_root() -> Path:
    return next(
        p
        for p in Path(__file__).resolve().parents
        if (p / "scripts" / "new-migration.py").exists()
    )


def _load_generator():
    script = _repo_root() / "scripts" / "new-migration.py"
    spec = importlib.util.spec_from_file_location("new_migration_generator", script)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


GEN = _load_generator()

_SLUG = "scaffold_smoke"


def _exec_scaffold(interface: str, tmp_path: Path):
    """Render a scaffold for *interface* and import it as a live module."""
    body = GEN._render_template(
        interface=interface,
        slug=_SLUG,
        description="scaffold smoke",
        timestamp=f"20260619_000000_{_SLUG}",
    )
    path = tmp_path / f"20260619_000000_{_SLUG}_{interface}.py"
    path.write_text(body, encoding="utf-8")
    spec = importlib.util.spec_from_file_location(f"scaffold_{interface}", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class TestPoolScaffold:
    """Convention A — ``up(pool)`` / ``down(pool)``."""

    def test_up_raises_not_implemented_naming_migration(self, tmp_path):
        module = _exec_scaffold("pool", tmp_path)
        with pytest.raises(NotImplementedError, match=_SLUG):
            asyncio.run(module.up(None))

    def test_down_raises_not_implemented_naming_migration(self, tmp_path):
        module = _exec_scaffold("pool", tmp_path)
        with pytest.raises(NotImplementedError, match=_SLUG):
            asyncio.run(module.down(None))

    def test_up_is_a_coroutine_function(self, tmp_path):
        # migrations_lint.py keys off the presence of an `up`/`run_migration`
        # def; the runner awaits it. Keep both true.
        module = _exec_scaffold("pool", tmp_path)
        assert inspect.iscoroutinefunction(module.up)
        assert inspect.iscoroutinefunction(module.down)


class TestConnScaffold:
    """Convention B (legacy) — ``run_migration(conn)`` / ``rollback_migration(conn)``."""

    def test_run_migration_raises_not_implemented_naming_migration(self, tmp_path):
        module = _exec_scaffold("conn", tmp_path)
        with pytest.raises(NotImplementedError, match=_SLUG):
            asyncio.run(module.run_migration(None))

    def test_rollback_migration_raises_not_implemented_naming_migration(self, tmp_path):
        module = _exec_scaffold("conn", tmp_path)
        with pytest.raises(NotImplementedError, match=_SLUG):
            asyncio.run(module.rollback_migration(None))

    def test_run_migration_is_a_coroutine_function(self, tmp_path):
        module = _exec_scaffold("conn", tmp_path)
        assert inspect.iscoroutinefunction(module.run_migration)
        assert inspect.iscoroutinefunction(module.rollback_migration)
