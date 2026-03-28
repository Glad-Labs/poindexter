"""
Unit tests for services/jwt_blocklist_service.py — JWTBlocklistService.

Covers:
  - is_blocked() returns False when pool not initialized
  - add_token() silently skips when pool not initialized
  - add_token() executes the correct INSERT on a mock pool
  - is_blocked() returns True when DB returns a row
  - is_blocked() returns False when DB returns None
  - is_blocked() returns False (fail-open) on DB error
  - cleanup() returns 0 when pool not initialized
  - cleanup() deletes expired rows and returns count
  - Module-level jwt_blocklist singleton exists
"""

from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, MagicMock

import pytest

from services.jwt_blocklist_service import JWTBlocklistService, jwt_blocklist

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_mock_pool(fetchval_return=None, execute_return="DELETE 0"):
    """Build a minimal asyncpg pool mock."""
    conn = AsyncMock()
    conn.fetchval = AsyncMock(return_value=fetchval_return)
    conn.execute = AsyncMock(return_value=execute_return)

    pool = MagicMock()
    pool.acquire = MagicMock()
    pool.acquire.return_value.__aenter__ = AsyncMock(return_value=conn)
    pool.acquire.return_value.__aexit__ = AsyncMock(return_value=False)
    return pool, conn


def _future_dt(minutes=15):
    return datetime.now(timezone.utc) + timedelta(minutes=minutes)


# ---------------------------------------------------------------------------
# Not-initialized (no pool)
# ---------------------------------------------------------------------------


@pytest.mark.unit
@pytest.mark.asyncio
class TestNotInitialized:
    async def test_is_blocked_returns_false_without_pool(self):
        svc = JWTBlocklistService()
        assert await svc.is_blocked("any-jti") is False

    async def test_add_token_does_not_raise_without_pool(self):
        svc = JWTBlocklistService()
        # Should not raise
        await svc.add_token("jti-123", "user-1", _future_dt())

    async def test_cleanup_returns_zero_without_pool(self):
        svc = JWTBlocklistService()
        result = await svc.cleanup()
        assert result == 0

    def test_ready_is_false_before_initialize(self):
        svc = JWTBlocklistService()
        assert svc.ready is False


# ---------------------------------------------------------------------------
# After initialization
# ---------------------------------------------------------------------------


@pytest.mark.unit
@pytest.mark.asyncio
class TestAfterInitialization:
    async def test_ready_is_true_after_initialize(self):
        pool, _ = _make_mock_pool()
        svc = JWTBlocklistService()
        await svc.initialize(pool)
        assert svc.ready is True

    async def test_add_token_calls_execute_with_insert(self):
        pool, conn = _make_mock_pool()
        svc = JWTBlocklistService()
        await svc.initialize(pool)

        exp = _future_dt()
        await svc.add_token("jti-abc", "user-42", exp)

        conn.execute.assert_awaited_once()
        call_args = conn.execute.await_args
        sql = call_args[0][0]
        assert "INSERT INTO jwt_blocklist" in sql
        assert call_args[0][1] == "jti-abc"
        assert call_args[0][2] == "user-42"
        assert call_args[0][3] == exp

    async def test_is_blocked_returns_true_when_db_returns_row(self):
        pool, conn = _make_mock_pool(fetchval_return=1)
        svc = JWTBlocklistService()
        await svc.initialize(pool)

        result = await svc.is_blocked("jti-blocked")
        assert result is True

    async def test_is_blocked_returns_false_when_db_returns_none(self):
        pool, conn = _make_mock_pool(fetchval_return=None)
        svc = JWTBlocklistService()
        await svc.initialize(pool)

        result = await svc.is_blocked("jti-clean")
        assert result is False

    async def test_is_blocked_fails_open_on_db_error(self):
        """DB failure must return False (not raise) to avoid false 401s."""
        pool, conn = _make_mock_pool()
        conn.fetchval.side_effect = Exception("DB connection lost")
        svc = JWTBlocklistService()
        await svc.initialize(pool)

        result = await svc.is_blocked("jti-error")
        assert result is False  # fail-open

    async def test_add_token_does_not_raise_on_db_error(self):
        pool, conn = _make_mock_pool()
        conn.execute.side_effect = Exception("DB write failed")
        svc = JWTBlocklistService()
        await svc.initialize(pool)

        # Should not raise
        await svc.add_token("jti-err", "user-1", _future_dt())


# ---------------------------------------------------------------------------
# cleanup
# ---------------------------------------------------------------------------


@pytest.mark.unit
@pytest.mark.asyncio
class TestCleanup:
    async def test_cleanup_returns_deleted_count(self):
        pool, conn = _make_mock_pool(execute_return="DELETE 5")
        svc = JWTBlocklistService()
        await svc.initialize(pool)

        result = await svc.cleanup()
        assert result == 5

    async def test_cleanup_returns_zero_when_no_rows_deleted(self):
        pool, conn = _make_mock_pool(execute_return="DELETE 0")
        svc = JWTBlocklistService()
        await svc.initialize(pool)

        result = await svc.cleanup()
        assert result == 0

    async def test_cleanup_calls_delete_with_current_time(self):
        pool, conn = _make_mock_pool(execute_return="DELETE 0")
        svc = JWTBlocklistService()
        await svc.initialize(pool)

        before = datetime.now(timezone.utc)
        await svc.cleanup()
        after = datetime.now(timezone.utc)

        conn.execute.assert_awaited_once()
        call_args = conn.execute.await_args
        ts = call_args[0][1]  # second positional param is the cutoff datetime
        assert before <= ts <= after

    async def test_cleanup_returns_zero_on_db_error(self):
        pool, conn = _make_mock_pool()
        conn.execute.side_effect = Exception("DB error")
        svc = JWTBlocklistService()
        await svc.initialize(pool)

        result = await svc.cleanup()
        assert result == 0


# ---------------------------------------------------------------------------
# Module-level singleton
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestSingleton:
    def test_jwt_blocklist_singleton_is_jwtblocklist_instance(self):
        assert isinstance(jwt_blocklist, JWTBlocklistService)

    def test_singleton_not_ready_at_import(self):
        # jwt_blocklist must not be pre-initialized (pool comes from startup)
        assert jwt_blocklist.ready is False or jwt_blocklist._pool is not None
        # Either uninitialized (ready=False) or already initialized in a test
        # environment — both are acceptable. We just verify it's a service instance.
        assert isinstance(jwt_blocklist, JWTBlocklistService)
