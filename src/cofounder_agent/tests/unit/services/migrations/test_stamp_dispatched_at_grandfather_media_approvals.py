"""Contract test for the defuse-grandfather-dispatch backfill migration.

The grandfather migrations bless already-live media as
``media_approvals.status='approved'`` (so a newly-gated RSS feed doesn't
freeze them) but leave ``dispatched_at IS NULL``. The upload dispatchers read
``approved AND dispatched_at IS NULL`` as "deliver now", so grandfathering
already-public videos re-uploaded them to YouTube (the 2026-06-15 incident).

This migration must stamp ``dispatched_at`` + ``dispatch_success`` on every
``approved`` grandfather row still ``dispatched_at IS NULL`` — declaring the
already-live media "distribution handled" so no dispatcher re-sends it. It
must scope to ``decided_by LIKE '%grandfather%'`` (never a deliberate
``operator:*`` queueing) and ``COALESCE`` so a replay clobbers nothing.
``down()`` is a deliberate one-way no-op — un-stamping would re-arm the
landmine.
"""
from __future__ import annotations

import importlib.util
from contextlib import asynccontextmanager
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest

_MIGRATION = (
    Path(__file__).resolve().parents[4]
    / "services"
    / "migrations"
    / "20260615_032708_stamp_dispatched_at_on_grandfather_media_approvals_to_defuse_re_dispatch.py"
)


def _load_migration():
    spec = importlib.util.spec_from_file_location("defuse_grandfather_mig", _MIGRATION)
    assert spec is not None and spec.loader is not None, f"cannot load {_MIGRATION}"
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _mock_pool():
    conn = MagicMock()
    conn.execute = AsyncMock(return_value="UPDATE 0")

    pool = MagicMock()

    @asynccontextmanager
    async def _acquire():
        yield conn

    pool.acquire = _acquire
    return pool, conn


def test_migration_file_exists():
    assert _MIGRATION.exists(), f"migration file missing: {_MIGRATION}"


@pytest.mark.asyncio
async def test_up_stamps_dispatched_on_grandfather_rows():
    mod = _load_migration()
    pool, conn = _mock_pool()

    await mod.up(pool)

    sql = conn.execute.call_args[0][0]
    assert "UPDATE media_approvals" in sql
    # The defusing verdict: declare distribution already handled.
    assert "dispatched_at" in sql
    assert "dispatch_success" in sql
    # Non-clobbering / idempotent — never overwrite a real prior stamp.
    assert "COALESCE" in sql
    # Scoped to grandfather provenance ONLY (never a deliberate operator push).
    assert "LIKE '%grandfather%'" in sql
    # Only already-approved, never-dispatched rows.
    assert "status = 'approved'" in sql
    assert "dispatched_at IS NULL" in sql


@pytest.mark.asyncio
async def test_down_is_one_way_noop():
    mod = _load_migration()
    pool, conn = _mock_pool()

    # Must not raise...
    await mod.down(pool)

    # ...and must NOT mutate: un-stamping would re-arm the re-dispatch landmine.
    conn.execute.assert_not_called()
