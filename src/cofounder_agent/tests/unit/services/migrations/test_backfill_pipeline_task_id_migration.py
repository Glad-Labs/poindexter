"""Tests for migration 20260528_021920 — backfill pipeline_task_id on
posts.metadata + sync stale pipeline_tasks.status.

Pins the SQL contract for the 2026-05-28 status-sync fix:

- Step 1 stamps ``metadata->>'pipeline_task_id'`` on every posts row
  that's missing the key, using the slug-suffix join (RIGHT(slug, 8))
  to find a unique matching pipeline_tasks row.
- Step 2 syncs stale ``pipeline_tasks.status`` to ``'published'`` for
  every posts row at ``status='published'`` whose linked task is still
  in an earlier state.

These tests exercise the SQL strings (not a live database) to confirm
the migration's shape stays load-bearing — particularly the
``HAVING COUNT(*) = 1`` ambiguity guard and the
``status IN ('approved', 'scheduled')`` restriction. The full E2E
behavior is verified via ``scripts/ci/migrations_smoke.py`` against a
fresh Postgres.
"""

from __future__ import annotations

import importlib.util
from contextlib import asynccontextmanager
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest


_MIGRATION_PATH = (
    Path(__file__).resolve().parents[4]
    / "services"
    / "migrations"
    / "20260528_021920_backfill_pipeline_task_id_on_posts_metadata.py"
)


def _load_migration():
    spec = importlib.util.spec_from_file_location(
        "m_20260528_021920", _MIGRATION_PATH,
    )
    assert spec is not None and spec.loader is not None
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _make_pool():
    """asyncpg pool stub that records execute calls. Each execute returns
    a benign ``UPDATE 0`` so the migration logging step doesn't crash."""
    conn = MagicMock()
    conn.execute = AsyncMock(return_value="UPDATE 0")

    # conn.transaction() returns an async context manager.
    txn_cm = MagicMock()
    txn_cm.__aenter__ = AsyncMock(return_value=conn)
    txn_cm.__aexit__ = AsyncMock(return_value=None)
    conn.transaction = MagicMock(return_value=txn_cm)

    pool = MagicMock()

    @asynccontextmanager
    async def _acquire():
        yield conn

    pool.acquire = _acquire
    return pool, conn


@pytest.mark.asyncio
async def test_up_issues_two_updates_in_a_transaction() -> None:
    """The migration runs both backfill steps inside a single transaction
    so a failure in step 2 rolls back step 1. Without the transaction
    wrap, a partial apply would leave stamped metadata but unsynced
    pipeline_tasks — operator-confusing."""
    mod = _load_migration()
    pool, conn = _make_pool()

    await mod.up(pool)

    # Two executes: the posts.metadata stamp + the pipeline_tasks sync.
    assert conn.execute.await_count == 2
    conn.transaction.assert_called_once()


@pytest.mark.asyncio
async def test_step_one_stamps_metadata_via_jsonb_set() -> None:
    """The stamp step uses ``jsonb_set`` so it preserves all other keys
    on the metadata column. The slug-suffix join (RIGHT(slug, 8)) is the
    same archaeology pattern as migration 20260519_134736."""
    mod = _load_migration()
    pool, conn = _make_pool()

    await mod.up(pool)

    stamp_sql = conn.execute.await_args_list[0].args[0]
    assert "UPDATE posts" in stamp_sql
    assert "jsonb_set" in stamp_sql
    assert "pipeline_task_id" in stamp_sql
    assert "RIGHT(p2.slug, 8)" in stamp_sql
    # Ambiguity guard: only stamp when exactly ONE pipeline_tasks row
    # matches the slug suffix.
    assert "HAVING COUNT(*) = 1" in stamp_sql
    # Idempotency: skip rows that already carry the key.
    assert "NOT (p2.metadata ? 'pipeline_task_id')" in stamp_sql


@pytest.mark.asyncio
async def test_step_two_syncs_pipeline_tasks_via_jsonb_seam() -> None:
    """Step 2 reads through the seam populated by step 1, joining via
    ``posts.metadata->>'pipeline_task_id'`` rather than slug archaeology.
    Restricted to source statuses ``'approved'`` / ``'scheduled'`` so it
    doesn't clobber rejected / failed tasks whose post somehow ended up
    published anyway (operator edge cases — surface to a human, don't
    auto-resolve)."""
    mod = _load_migration()
    pool, conn = _make_pool()

    await mod.up(pool)

    sync_sql = conn.execute.await_args_list[1].args[0]
    assert "UPDATE pipeline_tasks" in sync_sql
    assert "status = 'published'" in sync_sql
    assert "metadata ->> 'pipeline_task_id'" in sync_sql
    assert "p.status = 'published'" in sync_sql
    assert "pt.status IN ('approved', 'scheduled')" in sync_sql


@pytest.mark.asyncio
async def test_down_is_no_op() -> None:
    """One-way backfill — down is documented as a no-op rather than
    tearing the seam out (which would re-introduce the status-drift bug).
    The synced pipeline_tasks rows reflect reality (the posts ARE
    published), so leaving them is correct rollback behavior."""
    mod = _load_migration()
    pool, conn = _make_pool()

    await mod.down(pool)

    assert conn.execute.await_count == 0
