"""Contract test for the grandfather-video-media_approvals migration.

The migration ships in the PR that gates the video RSS feed on an approved
``media_approvals`` row. It must INSERT ``status='approved'`` rows stamped
``decided_by='auto:grandfather'`` for already-live videos that lack one, and
``down()`` must delete ONLY those grandfather rows (never a real operator
decision). Idempotency lives in the ``NOT EXISTS`` + ``ON CONFLICT`` guards.
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
    / "20260615_014648_grandfather_video_media_approvals_for_already_live_videos.py"
)


def _load_migration():
    spec = importlib.util.spec_from_file_location("grandfather_video_mig", _MIGRATION)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _mock_pool():
    conn = MagicMock()
    conn.execute = AsyncMock(return_value="INSERT 0 0")

    pool = MagicMock()

    @asynccontextmanager
    async def _acquire():
        yield conn

    pool.acquire = _acquire
    return pool, conn


def test_migration_file_exists():
    assert _MIGRATION.exists(), f"migration file missing: {_MIGRATION}"


@pytest.mark.asyncio
async def test_up_inserts_approved_grandfather_rows():
    mod = _load_migration()
    pool, conn = _mock_pool()

    await mod.up(pool)

    sql = conn.execute.call_args[0][0]
    assert "INSERT INTO media_approvals" in sql
    # The grandfather verdict: approved, provenance-stamped.
    assert "'approved'" in sql
    assert "'auto:grandfather'" in sql
    # Long-form video medium + both legacy and Stage-2 asset types.
    assert "'video'" in sql
    assert "'video_long'" in sql
    # Only published posts with a video asset and NO existing decision row.
    assert "status = 'published'" in sql
    assert "NOT EXISTS" in sql
    # Idempotent replay guard (never clobbers a prior operator decision).
    assert "ON CONFLICT (post_id, medium) DO NOTHING" in sql


@pytest.mark.asyncio
async def test_down_deletes_only_grandfather_rows():
    mod = _load_migration()
    pool, conn = _mock_pool()

    await mod.down(pool)

    sql = conn.execute.call_args[0][0]
    assert "DELETE FROM media_approvals" in sql
    # Scoped to the grandfather provenance so a rollback never removes a real
    # operator approval.
    assert "'auto:grandfather'" in sql
    assert "medium = 'video'" in sql
