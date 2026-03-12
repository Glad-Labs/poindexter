"""
Unit tests for utils.connection_health module.

All tests are pure — zero real DB connections.
The asyncpg pool is mocked throughout.
Covers ConnectionPoolHealth status checks, is_degraded/critical detection,
and diagnose_connection_issues utility.
"""

import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from utils.connection_health import ConnectionPoolHealth, diagnose_connection_issues


def _make_mock_pool(pool_size: int = 10, idle_size: int = 8) -> MagicMock:
    """Create a minimal mock asyncpg pool."""
    pool = MagicMock()
    pool.get_size.return_value = pool_size
    pool.get_idle_size.return_value = idle_size

    # Mock async context manager for pool.acquire()
    conn = AsyncMock()
    conn.fetchval = AsyncMock(return_value=1)

    acquire_ctx = MagicMock()
    acquire_ctx.__aenter__ = AsyncMock(return_value=conn)
    acquire_ctx.__aexit__ = AsyncMock(return_value=False)
    pool.acquire = MagicMock(return_value=acquire_ctx)

    return pool


# ---------------------------------------------------------------------------
# ConnectionPoolHealth — initialization
# ---------------------------------------------------------------------------


class TestConnectionPoolHealthInit:
    def test_default_check_interval(self):
        pool = _make_mock_pool()
        monitor = ConnectionPoolHealth(pool)
        assert monitor.check_interval == 60

    def test_custom_check_interval(self):
        pool = _make_mock_pool()
        monitor = ConnectionPoolHealth(pool, check_interval=30)
        assert monitor.check_interval == 30

    def test_initial_state_has_no_last_check(self):
        pool = _make_mock_pool()
        monitor = ConnectionPoolHealth(pool)
        assert monitor.last_check_status is None
        assert monitor.last_check_time is None

    def test_initial_consecutive_failures_zero(self):
        pool = _make_mock_pool()
        monitor = ConnectionPoolHealth(pool)
        assert monitor.consecutive_failures == 0


# ---------------------------------------------------------------------------
# ConnectionPoolHealth — check_pool_health (success)
# ---------------------------------------------------------------------------


class TestCheckPoolHealthSuccess:
    @pytest.mark.asyncio
    async def test_healthy_pool_returns_healthy_true(self):
        pool = _make_mock_pool(pool_size=10, idle_size=8)
        monitor = ConnectionPoolHealth(pool)
        status = await monitor.check_pool_health()
        assert status["healthy"] is True

    @pytest.mark.asyncio
    async def test_healthy_pool_reports_pool_stats(self):
        pool = _make_mock_pool(pool_size=10, idle_size=7)
        monitor = ConnectionPoolHealth(pool)
        status = await monitor.check_pool_health()
        assert status["pool_size"] == 10
        assert status["pool_idle"] == 7
        assert status["pool_used"] == 3

    @pytest.mark.asyncio
    async def test_healthy_pool_resets_consecutive_failures(self):
        pool = _make_mock_pool()
        monitor = ConnectionPoolHealth(pool)
        monitor.consecutive_failures = 5
        await monitor.check_pool_health()
        assert monitor.consecutive_failures == 0

    @pytest.mark.asyncio
    async def test_healthy_pool_updates_last_check_status(self):
        pool = _make_mock_pool()
        monitor = ConnectionPoolHealth(pool)
        assert monitor.last_check_status is None
        await monitor.check_pool_health()
        assert monitor.last_check_status is not None

    @pytest.mark.asyncio
    async def test_status_contains_check_duration(self):
        pool = _make_mock_pool()
        monitor = ConnectionPoolHealth(pool)
        status = await monitor.check_pool_health()
        assert "check_duration_ms" in status

    @pytest.mark.asyncio
    async def test_status_contains_timestamp(self):
        pool = _make_mock_pool()
        monitor = ConnectionPoolHealth(pool)
        status = await monitor.check_pool_health()
        assert "timestamp" in status


# ---------------------------------------------------------------------------
# ConnectionPoolHealth — check_pool_health (none pool)
# ---------------------------------------------------------------------------


class TestCheckPoolHealthNonePool:
    @pytest.mark.asyncio
    async def test_none_pool_returns_not_healthy(self):
        monitor = ConnectionPoolHealth(None)  # type: ignore[arg-type]
        status = await monitor.check_pool_health()
        assert status["healthy"] is False
        assert "not initialized" in status["reason"]


# ---------------------------------------------------------------------------
# ConnectionPoolHealth — check_pool_health (timeout)
# ---------------------------------------------------------------------------


class TestCheckPoolHealthTimeout:
    @pytest.mark.asyncio
    async def test_timeout_returns_not_healthy(self):
        pool = MagicMock()

        # Make acquire() raise TimeoutError
        acquire_ctx = MagicMock()
        acquire_ctx.__aenter__ = AsyncMock(side_effect=asyncio.TimeoutError())
        acquire_ctx.__aexit__ = AsyncMock(return_value=False)
        pool.acquire = MagicMock(return_value=acquire_ctx)

        monitor = ConnectionPoolHealth(pool)
        status = await monitor.check_pool_health()
        assert status["healthy"] is False

    @pytest.mark.asyncio
    async def test_timeout_increments_consecutive_failures(self):
        pool = MagicMock()
        acquire_ctx = MagicMock()
        acquire_ctx.__aenter__ = AsyncMock(side_effect=asyncio.TimeoutError())
        acquire_ctx.__aexit__ = AsyncMock(return_value=False)
        pool.acquire = MagicMock(return_value=acquire_ctx)

        monitor = ConnectionPoolHealth(pool)
        await monitor.check_pool_health()
        assert monitor.consecutive_failures == 1


# ---------------------------------------------------------------------------
# ConnectionPoolHealth — check_pool_health (generic exception)
# ---------------------------------------------------------------------------


class TestCheckPoolHealthGenericError:
    @pytest.mark.asyncio
    async def test_generic_exception_returns_not_healthy(self):
        pool = MagicMock()
        acquire_ctx = MagicMock()
        acquire_ctx.__aenter__ = AsyncMock(side_effect=RuntimeError("db down"))
        acquire_ctx.__aexit__ = AsyncMock(return_value=False)
        pool.acquire = MagicMock(return_value=acquire_ctx)

        monitor = ConnectionPoolHealth(pool)
        status = await monitor.check_pool_health()
        assert status["healthy"] is False

    @pytest.mark.asyncio
    async def test_generic_exception_increments_failures(self):
        pool = MagicMock()
        acquire_ctx = MagicMock()
        acquire_ctx.__aenter__ = AsyncMock(side_effect=RuntimeError("error"))
        acquire_ctx.__aexit__ = AsyncMock(return_value=False)
        pool.acquire = MagicMock(return_value=acquire_ctx)

        monitor = ConnectionPoolHealth(pool)
        await monitor.check_pool_health()
        assert monitor.consecutive_failures == 1


# ---------------------------------------------------------------------------
# ConnectionPoolHealth — get_health_summary
# ---------------------------------------------------------------------------


class TestGetHealthSummary:
    def test_no_checks_performed_returns_unknown(self):
        pool = _make_mock_pool()
        monitor = ConnectionPoolHealth(pool)
        summary = monitor.get_health_summary()
        assert summary["status"] == "unknown"

    @pytest.mark.asyncio
    async def test_after_check_returns_status(self):
        pool = _make_mock_pool()
        monitor = ConnectionPoolHealth(pool)
        await monitor.check_pool_health()
        summary = monitor.get_health_summary()
        assert "healthy" in summary


# ---------------------------------------------------------------------------
# ConnectionPoolHealth — is_pool_degraded
# ---------------------------------------------------------------------------


class TestIsPoolDegraded:
    def test_no_checks_returns_false(self):
        monitor = ConnectionPoolHealth(_make_mock_pool())
        assert monitor.is_pool_degraded() is False

    def test_two_consecutive_failures_is_degraded(self):
        monitor = ConnectionPoolHealth(_make_mock_pool())
        monitor.consecutive_failures = 2
        monitor.last_check_status = {"pool_used": 0, "pool_size": 10}
        assert monitor.is_pool_degraded() is True

    def test_high_utilization_is_degraded(self):
        monitor = ConnectionPoolHealth(_make_mock_pool())
        monitor.last_check_status = {"pool_used": 9, "pool_size": 10}  # 90% utilization
        assert monitor.is_pool_degraded() is True

    def test_low_utilization_not_degraded(self):
        monitor = ConnectionPoolHealth(_make_mock_pool())
        monitor.last_check_status = {"pool_used": 2, "pool_size": 10}  # 20% utilization
        assert monitor.is_pool_degraded() is False


# ---------------------------------------------------------------------------
# ConnectionPoolHealth — is_pool_critical
# ---------------------------------------------------------------------------


class TestIsPoolCritical:
    def test_no_checks_returns_false(self):
        monitor = ConnectionPoolHealth(_make_mock_pool())
        assert monitor.is_pool_critical() is False

    def test_max_consecutive_failures_is_critical(self):
        monitor = ConnectionPoolHealth(_make_mock_pool())
        monitor.consecutive_failures = monitor.max_consecutive_failures
        monitor.last_check_status = {"pool_idle": 1}
        assert monitor.is_pool_critical() is True

    def test_no_idle_connections_is_critical(self):
        monitor = ConnectionPoolHealth(_make_mock_pool())
        monitor.last_check_status = {"pool_idle": 0}
        assert monitor.is_pool_critical() is True

    def test_healthy_pool_not_critical(self):
        monitor = ConnectionPoolHealth(_make_mock_pool())
        monitor.last_check_status = {"pool_idle": 5}
        monitor.consecutive_failures = 0
        assert monitor.is_pool_critical() is False


# ---------------------------------------------------------------------------
# diagnose_connection_issues
# ---------------------------------------------------------------------------


class TestDiagnoseConnectionIssues:
    @pytest.mark.asyncio
    async def test_returns_dict_with_required_keys(self):
        result = await diagnose_connection_issues()
        assert "timestamp" in result
        assert "issues" in result
        assert "recommendations" in result

    @pytest.mark.asyncio
    async def test_missing_database_url_reported(self):
        with patch.dict("os.environ", {}, clear=True):
            # Ensure DATABASE_URL is absent
            import os
            os.environ.pop("DATABASE_URL", None)
            result = await diagnose_connection_issues()
        issues_text = " ".join(result["issues"])
        assert "DATABASE_URL" in issues_text

    @pytest.mark.asyncio
    async def test_invalid_pool_config_reported(self):
        env_patch = {
            "DATABASE_URL": "postgresql://localhost/db",
            "DATABASE_POOL_MIN_SIZE": "50",
            "DATABASE_POOL_MAX_SIZE": "10",  # min > max — invalid
        }
        with patch.dict("os.environ", env_patch):
            result = await diagnose_connection_issues()
        issues_text = " ".join(result["issues"])
        assert "pool" in issues_text.lower() or "min" in issues_text.lower()

    @pytest.mark.asyncio
    async def test_valid_config_has_no_pool_issues(self):
        env_patch = {
            "DATABASE_URL": "postgresql://localhost/db",
            "DATABASE_POOL_MIN_SIZE": "5",
            "DATABASE_POOL_MAX_SIZE": "20",
        }
        with patch.dict("os.environ", env_patch):
            result = await diagnose_connection_issues()
        # Should have no pool config issues (possibly just no DATABASE_URL if also patched)
        issues_text = " ".join(result["issues"])
        assert "Invalid pool config" not in issues_text
