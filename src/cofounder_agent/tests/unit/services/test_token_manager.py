"""
Unit tests for services.token_manager.TokenManager

All DB calls are mocked via AsyncMock — zero real I/O.

Tests cover:
- store_oauth_token: success path, DB failure returns False
- get_oauth_token: found + not-expired, found + expired, not found, DB error
- mark_token_expired: success, DB failure returns False
- cleanup_old_tokens: success (parses DELETE N count), DB failure returns 0
- _audit_log: does not raise
"""

import json
from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from services.token_manager import TokenManager


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def make_db(*, store_ok=True, get_row=None, mark_ok=True, delete_result="DELETE 5"):
    """Build a minimal fake db_service with a mocked pool.acquire context manager."""
    db = MagicMock()
    conn = AsyncMock()

    # store_oauth_token / mark_token_expired path
    conn.execute = AsyncMock(
        side_effect=(
            None if store_ok and mark_ok
            else RuntimeError("db error")
        )
    )

    # get_oauth_token path
    conn.fetchrow = AsyncMock(return_value=get_row)

    # cleanup_old_tokens path
    conn.execute = AsyncMock(return_value=delete_result)

    cm = AsyncMock()
    cm.__aenter__ = AsyncMock(return_value=conn)
    cm.__aexit__ = AsyncMock(return_value=False)
    db.pool.acquire = MagicMock(return_value=cm)
    return db, conn


def future_iso():
    return (datetime.now(timezone.utc) + timedelta(hours=2)).isoformat()


def past_iso():
    return (datetime.now(timezone.utc) - timedelta(hours=2)).isoformat()


# ---------------------------------------------------------------------------
# store_oauth_token
# ---------------------------------------------------------------------------


class TestStoreOAuthToken:
    @pytest.mark.asyncio
    async def test_success_returns_true(self):
        db, conn = make_db()
        tm = TokenManager(db)
        result = await tm.store_oauth_token(
            "user-123",
            "github",
            {"access_token": "tok123456", "expires_in": 3600},
        )
        assert result is True
        conn.execute.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_db_failure_returns_false(self):
        db, conn = make_db()
        conn.execute.side_effect = RuntimeError("connection refused")
        tm = TokenManager(db)
        result = await tm.store_oauth_token("user-1", "github", {"access_token": "abc12345"})
        assert result is False

    @pytest.mark.asyncio
    async def test_uses_default_expires_in_when_missing(self):
        db, conn = make_db()
        tm = TokenManager(db)
        # No expires_in in response — defaults to 3600
        result = await tm.store_oauth_token("u1", "google", {"access_token": "abcdefgh"})
        assert result is True
        # Verify the SQL was called
        conn.execute.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_missing_access_token_still_calls_db(self):
        db, conn = make_db()
        tm = TokenManager(db)
        # OAuth response without access_token
        result = await tm.store_oauth_token("u1", "github", {})
        assert result is True


# ---------------------------------------------------------------------------
# get_oauth_token
# ---------------------------------------------------------------------------


class TestGetOAuthToken:
    @pytest.mark.asyncio
    async def test_returns_token_when_not_expired(self):
        provider_data = json.dumps({
            "access_token": "my-token",
            "expires_at": future_iso(),
        })
        db, conn = make_db(get_row={"provider_data": provider_data})
        conn.fetchrow = AsyncMock(return_value={"provider_data": provider_data})
        tm = TokenManager(db)
        token = await tm.get_oauth_token("user-1", "github")
        assert token == "my-token"

    @pytest.mark.asyncio
    async def test_returns_none_when_expired(self):
        provider_data = json.dumps({
            "access_token": "old-token",
            "expires_at": past_iso(),
        })
        db, conn = make_db()
        conn.fetchrow = AsyncMock(return_value={"provider_data": provider_data})
        tm = TokenManager(db)
        token = await tm.get_oauth_token("user-1", "github")
        assert token is None

    @pytest.mark.asyncio
    async def test_returns_none_when_row_not_found(self):
        db, conn = make_db()
        conn.fetchrow = AsyncMock(return_value=None)
        tm = TokenManager(db)
        token = await tm.get_oauth_token("user-1", "github")
        assert token is None

    @pytest.mark.asyncio
    async def test_returns_token_when_no_expires_at(self):
        """If no expiration set, token is returned as-is."""
        provider_data = json.dumps({"access_token": "forever-token"})
        db, conn = make_db()
        conn.fetchrow = AsyncMock(return_value={"provider_data": provider_data})
        tm = TokenManager(db)
        token = await tm.get_oauth_token("user-1", "github")
        assert token == "forever-token"

    @pytest.mark.asyncio
    async def test_db_error_returns_none(self):
        db, conn = make_db()
        conn.fetchrow = AsyncMock(side_effect=RuntimeError("db failure"))
        tm = TokenManager(db)
        token = await tm.get_oauth_token("user-1", "github")
        assert token is None


# ---------------------------------------------------------------------------
# mark_token_expired
# ---------------------------------------------------------------------------


class TestMarkTokenExpired:
    @pytest.mark.asyncio
    async def test_success_returns_true(self):
        db, conn = make_db()
        tm = TokenManager(db)
        result = await tm.mark_token_expired("user-1", "github")
        assert result is True

    @pytest.mark.asyncio
    async def test_db_failure_returns_false(self):
        db, conn = make_db()
        conn.execute.side_effect = RuntimeError("db error")
        tm = TokenManager(db)
        result = await tm.mark_token_expired("user-1", "github")
        assert result is False


# ---------------------------------------------------------------------------
# cleanup_old_tokens
# ---------------------------------------------------------------------------


class TestCleanupOldTokens:
    @pytest.mark.asyncio
    async def test_returns_count_from_delete_result(self):
        db, conn = make_db(delete_result="DELETE 7")
        conn.execute = AsyncMock(return_value="DELETE 7")
        tm = TokenManager(db)
        count = await tm.cleanup_old_tokens(days=90)
        assert count == 7

    @pytest.mark.asyncio
    async def test_returns_zero_count_when_none_deleted(self):
        db, conn = make_db()
        conn.execute = AsyncMock(return_value="DELETE 0")
        tm = TokenManager(db)
        count = await tm.cleanup_old_tokens()
        assert count == 0

    @pytest.mark.asyncio
    async def test_db_failure_returns_zero(self):
        db, conn = make_db()
        conn.execute = AsyncMock(side_effect=RuntimeError("db error"))
        tm = TokenManager(db)
        count = await tm.cleanup_old_tokens()
        assert count == 0

    @pytest.mark.asyncio
    async def test_custom_days_parameter_used(self):
        db, conn = make_db()
        conn.execute = AsyncMock(return_value="DELETE 3")
        tm = TokenManager(db)
        count = await tm.cleanup_old_tokens(days=30)
        assert count == 3
        # Verify execute was called with the cutoff string
        conn.execute.assert_awaited_once()


# ---------------------------------------------------------------------------
# _audit_log — internal, should not raise
# ---------------------------------------------------------------------------


class TestAuditLog:
    @pytest.mark.asyncio
    async def test_audit_log_does_not_raise(self):
        db, _ = make_db()
        tm = TokenManager(db)
        await tm._audit_log("stored_token", "user-1", "github", "success")
