"""Unit tests for ``services/jobs/expire_stale_approvals.py``.

Pool is mocked; focuses on: config → app_settings fallback → default
precedence, UPDATE parameter pass-through, JobResult shape.
"""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest

from services.jobs.expire_stale_approvals import ExpireStaleApprovalsJob


def _make_mock_pool(
    *,
    setting_value: str | None = None,
    expired_rows: list[dict] | None = None,
    fetchval_raises: BaseException | None = None,
    fetch_raises: BaseException | None = None,
) -> Any:
    """Return a pool whose acquire() yields a conn with fetchval + fetch stubbed."""
    conn = AsyncMock()
    if fetchval_raises is not None:
        conn.fetchval = AsyncMock(side_effect=fetchval_raises)
    else:
        conn.fetchval = AsyncMock(return_value=setting_value)
    if fetch_raises is not None:
        conn.fetch = AsyncMock(side_effect=fetch_raises)
    else:
        conn.fetch = AsyncMock(return_value=expired_rows or [])

    ctx = AsyncMock()
    ctx.__aenter__ = AsyncMock(return_value=conn)
    ctx.__aexit__ = AsyncMock(return_value=False)

    pool = MagicMock()
    pool.acquire = MagicMock(return_value=ctx)
    return pool, conn


class TestContract:
    def test_has_required_attrs(self):
        job = ExpireStaleApprovalsJob()
        assert job.name == "expire_stale_approvals"
        assert job.schedule == "every 6 hours"
        assert job.idempotent is True


class TestRun:
    @pytest.mark.asyncio
    async def test_uses_config_ttl_first(self):
        """config.ttl_days beats everything (no app_settings lookup)."""
        pool, conn = _make_mock_pool(setting_value="99", expired_rows=[])
        job = ExpireStaleApprovalsJob()
        result = await job.run(pool, {"ttl_days": 3})

        assert result.ok is True
        # Only fetch() should be called — fetchval should be skipped.
        conn.fetchval.assert_not_awaited()
        # TTL=3 should be the $1 parameter on the UPDATE.
        args = conn.fetch.call_args.args
        assert args[1] == 3

    @pytest.mark.asyncio
    async def test_falls_back_to_app_settings_key(self):
        pool, conn = _make_mock_pool(setting_value="14", expired_rows=[])
        job = ExpireStaleApprovalsJob()
        result = await job.run(pool, {})

        assert result.ok is True
        conn.fetchval.assert_awaited_once()
        args = conn.fetch.call_args.args
        assert args[1] == 14

    @pytest.mark.asyncio
    async def test_defaults_to_7_days_when_nothing_configured(self):
        pool, conn = _make_mock_pool(setting_value=None, expired_rows=[])
        job = ExpireStaleApprovalsJob()
        result = await job.run(pool, {})

        assert result.ok is True
        args = conn.fetch.call_args.args
        assert args[1] == 7
        assert "7d" in result.detail

    @pytest.mark.asyncio
    async def test_app_settings_lookup_failure_falls_through_to_default(self):
        """If the fetchval raises, we must log + keep going (not crash the job)."""
        pool, _ = _make_mock_pool(
            fetchval_raises=RuntimeError("app_settings table missing"),
            expired_rows=[],
        )
        job = ExpireStaleApprovalsJob()
        result = await job.run(pool, {})

        # The fetch still runs with default ttl = 7.
        assert result.ok is True
        assert "7d" in result.detail

    @pytest.mark.asyncio
    async def test_returns_changes_made_equal_to_expired_count(self):
        expired = [
            {"task_id": f"t-{i}", "topic": f"topic {i}"}
            for i in range(5)
        ]
        pool, _ = _make_mock_pool(setting_value="7", expired_rows=expired)
        job = ExpireStaleApprovalsJob()
        result = await job.run(pool, {"ttl_days": 7})

        assert result.ok is True
        assert result.changes_made == 5
        assert "5 task(s)" in result.detail

    @pytest.mark.asyncio
    async def test_zero_expired_still_ok(self):
        pool, _ = _make_mock_pool(expired_rows=[])
        job = ExpireStaleApprovalsJob()
        result = await job.run(pool, {"ttl_days": 7})

        assert result.ok is True
        assert result.changes_made == 0

    @pytest.mark.asyncio
    async def test_update_failure_returns_not_ok(self):
        """If the UPDATE raises, surface it in JobResult rather than crashing."""
        pool, _ = _make_mock_pool(
            setting_value="7",
            fetch_raises=RuntimeError("deadlock detected"),
        )
        job = ExpireStaleApprovalsJob()
        result = await job.run(pool, {"ttl_days": 7})

        assert result.ok is False
        assert result.changes_made == 0
        assert "deadlock" in result.detail
