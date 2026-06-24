"""Tests for retention_runner per-policy min_interval_hours throttle."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, patch

import pytest

from services.integrations.retention_runner import run_all


def _row(name: str, *, min_interval_hours=None, last_run_at=None):
    return {
        "id": "test-uuid",
        "name": name,
        "handler_name": "ttl_prune",
        "min_interval_hours": min_interval_hours,
        "last_run_at": last_run_at,
    }


@pytest.mark.asyncio
async def test_skip_when_recently_run():
    """Policy with min_interval_hours=1 that ran 30 min ago is skipped."""
    recent = datetime.now(timezone.utc) - timedelta(minutes=30)
    row = _row("test.policy", min_interval_hours=1.0, last_run_at=recent)

    with patch(
        "services.integrations.retention_runner._load_enabled_policies",
        new=AsyncMock(return_value=[row]),
    ):
        summary = await run_all(pool=None)

    assert summary.total_deleted == 0
    assert summary.total_failed == 0
    assert len(summary.policies) == 0  # skipped rows don't appear in results


@pytest.mark.asyncio
async def test_not_skipped_when_overdue():
    """Policy with min_interval_hours=1 that ran 2 hours ago is dispatched."""
    old = datetime.now(timezone.utc) - timedelta(hours=2)
    row = _row("test.policy", min_interval_hours=1.0, last_run_at=old)

    dispatch_result = {"deleted": 5}
    with (
        patch(
            "services.integrations.retention_runner._load_enabled_policies",
            new=AsyncMock(return_value=[row]),
        ),
        patch(
            "services.integrations.retention_runner.registry.dispatch",
            new=AsyncMock(return_value=dispatch_result),
        ),
        patch(
            "services.integrations.retention_runner._record_success",
            new=AsyncMock(),
        ),
    ):
        summary = await run_all(pool=None)

    assert len(summary.policies) == 1
    assert summary.policies[0].name == "test.policy"
    assert summary.total_deleted == 5


@pytest.mark.asyncio
async def test_no_interval_always_runs():
    """Policy with min_interval_hours=None is never skipped."""
    row = _row("test.policy", min_interval_hours=None, last_run_at=None)

    dispatch_result = {"deleted": 0}
    with (
        patch(
            "services.integrations.retention_runner._load_enabled_policies",
            new=AsyncMock(return_value=[row]),
        ),
        patch(
            "services.integrations.retention_runner.registry.dispatch",
            new=AsyncMock(return_value=dispatch_result),
        ),
        patch(
            "services.integrations.retention_runner._record_success",
            new=AsyncMock(),
        ),
    ):
        summary = await run_all(pool=None)

    assert len(summary.policies) == 1
    assert summary.policies[0].name == "test.policy"


@pytest.mark.asyncio
async def test_no_interval_but_has_last_run_at_still_runs():
    """Policy with last_run_at but no min_interval_hours is never throttled."""
    recent = datetime.now(timezone.utc) - timedelta(minutes=1)
    row = _row("test.policy", min_interval_hours=None, last_run_at=recent)

    dispatch_result = {"deleted": 3}
    with (
        patch(
            "services.integrations.retention_runner._load_enabled_policies",
            new=AsyncMock(return_value=[row]),
        ),
        patch(
            "services.integrations.retention_runner.registry.dispatch",
            new=AsyncMock(return_value=dispatch_result),
        ),
        patch(
            "services.integrations.retention_runner._record_success",
            new=AsyncMock(),
        ),
    ):
        summary = await run_all(pool=None)

    assert len(summary.policies) == 1


@pytest.mark.asyncio
async def test_timezone_naive_last_run_at_handled():
    """Timezone-naive last_run_at (e.g. from older DB rows) doesn't crash the check.

    The skip-check coerces naive datetimes to UTC via .replace(), which may not
    correctly reflect the machine timezone — but the critical invariant is that
    the comparison never raises a TypeError, and either outcome (skip or run) is
    acceptable.
    """
    naive_recent = datetime.now() - timedelta(minutes=30)  # naive, no tz
    row = _row("test.policy", min_interval_hours=1.0, last_run_at=naive_recent)

    with (
        patch(
            "services.integrations.retention_runner._load_enabled_policies",
            new=AsyncMock(return_value=[row]),
        ),
        patch(
            "services.integrations.retention_runner.registry.dispatch",
            new=AsyncMock(return_value={"deleted": 0}),
        ),
        patch(
            "services.integrations.retention_runner._record_success",
            new=AsyncMock(),
        ),
    ):
        # Should not raise TypeError from naive vs aware comparison
        summary = await run_all(pool=None)

    assert isinstance(summary.total_deleted, int)
