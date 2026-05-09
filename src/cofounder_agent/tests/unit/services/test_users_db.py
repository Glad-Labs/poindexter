"""
Unit tests for services/users_db.py.

Tests cover:
- UsersDatabase.get_user_by_id — found / not found
- UsersDatabase.get_user_by_email — found / not found
- UsersDatabase.get_user_by_username — found / not found
- UsersDatabase.create_user — success / DB error
- UsersDatabase.get_or_create_oauth_user — 3 paths:
    1. OAuth account already linked → existing user returned
    2. Email exists, no OAuth → link OAuth to existing user
    3. Neither exists → create new user + OAuth account
- UsersDatabase.get_oauth_accounts — found / empty
- UsersDatabase.unlink_oauth_account — success / not found / DB error

asyncpg pool fully mocked; no real DB access.
"""

from contextlib import asynccontextmanager
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from services.users_db import UsersDatabase

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_row(**kwargs):
    """Create a mock asyncpg Record-like row.

    Strict ``__getitem__`` (KeyError on missing key) so production code
    that reads a column the test didn't set fails loudly instead of
    silently getting ``None`` and passing — see GH#337.

    Use this helper ONLY when production code reads ``row[<key>]`` — the
    strict mapping is what gives the test signal value. When a test
    just hands the row to a patched ``ModelConverter`` and asserts on
    the converter's return value, prefer ``object()`` directly: a
    literal sentinel makes it obvious the row contents are not under
    test, and prevents the row-faker from quietly accumulating stale
    columns over time (the original symptom in GH#30).
    """
    row = MagicMock()
    _data = {**kwargs}
    row.__getitem__ = lambda self, k, _d=_data: _d[k]
    row.get = lambda k, default=None, _d=_data: _d.get(k, default)
    row.__bool__ = lambda self: True
    return row


def _make_pool(
    fetchrow_results=None,  # list returned in sequence
    fetch_result=None,
    execute_result=None,
    fetchrow_side_effect=None,
):
    conn = MagicMock()
    if fetchrow_side_effect:
        conn.fetchrow = AsyncMock(side_effect=fetchrow_side_effect)
    elif fetchrow_results is not None:
        conn.fetchrow = AsyncMock(side_effect=list(fetchrow_results))
    else:
        conn.fetchrow = AsyncMock(return_value=None)
    conn.fetch = AsyncMock(return_value=fetch_result or [])
    conn.execute = AsyncMock(return_value=execute_result or "DELETE 1")

    # Mock conn.transaction() as a no-op async context manager so tests work
    # with the REPEATABLE READ transaction wrapper added in issue #767 fix.
    @asynccontextmanager
    async def _transaction(**_kwargs):
        yield

    conn.transaction = _transaction

    pool = MagicMock()

    @asynccontextmanager
    async def _acquire():
        yield conn

    pool.acquire = _acquire
    return pool


def _make_db(pool=None):
    return UsersDatabase(pool=pool or _make_pool())


_CONVERTER = "services.users_db.ModelConverter"


# ---------------------------------------------------------------------------
# get_user_by_id
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestGetUserById:
    @pytest.mark.asyncio
    async def test_found_returns_user_response(self):
        # Opaque row — get_user_by_id passes whatever fetchrow returns
        # straight to ModelConverter.to_user_response, which is patched
        # below. The row's column shape is not under test here.
        pool = _make_pool(fetchrow_results=[object()])
        db = _make_db(pool)

        sentinel = object()
        with patch(f"{_CONVERTER}.to_user_response", return_value=sentinel):
            result = await db.get_user_by_id("user-uuid")

        assert result is sentinel

    @pytest.mark.asyncio
    async def test_not_found_returns_none(self):
        pool = _make_pool(fetchrow_results=[None])
        db = _make_db(pool)

        with patch(f"{_CONVERTER}.to_user_response", return_value=None):
            result = await db.get_user_by_id("no-such-user")

        assert result is None


# ---------------------------------------------------------------------------
# get_user_by_email
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestGetUserByEmail:
    @pytest.mark.asyncio
    async def test_found_returns_user_response(self):
        # Opaque row — column shape not under test (see GH#337).
        pool = _make_pool(fetchrow_results=[object()])
        db = _make_db(pool)

        sentinel = object()
        with patch(f"{_CONVERTER}.to_user_response", return_value=sentinel):
            result = await db.get_user_by_email("alice@example.com")

        assert result is sentinel

    @pytest.mark.asyncio
    async def test_not_found_returns_none(self):
        pool = _make_pool(fetchrow_results=[None])
        db = _make_db(pool)

        with patch(f"{_CONVERTER}.to_user_response", return_value=None):
            result = await db.get_user_by_email("nobody@example.com")

        assert result is None


# ---------------------------------------------------------------------------
# get_user_by_username
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestGetUserByUsername:
    @pytest.mark.asyncio
    async def test_found_returns_user_response(self):
        # Opaque row — column shape not under test (see GH#337).
        pool = _make_pool(fetchrow_results=[object()])
        db = _make_db(pool)

        sentinel = object()
        with patch(f"{_CONVERTER}.to_user_response", return_value=sentinel):
            result = await db.get_user_by_username("alice")

        assert result is sentinel

    @pytest.mark.asyncio
    async def test_not_found_returns_none(self):
        pool = _make_pool(fetchrow_results=[None])
        db = _make_db(pool)

        with patch(f"{_CONVERTER}.to_user_response", return_value=None):
            result = await db.get_user_by_username("nobody")

        assert result is None


# ---------------------------------------------------------------------------
# create_user
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestCreateUser:
    @pytest.mark.asyncio
    async def test_success_returns_user_response(self):
        # Opaque row — production hands fetchrow's result straight to
        # the patched converter without reading any column.
        pool = _make_pool(fetchrow_results=[object()])
        db = _make_db(pool)

        sentinel = object()
        with patch(f"{_CONVERTER}.to_user_response", return_value=sentinel):
            result = await db.create_user({"email": "bob@example.com", "username": "bob"})

        assert result is sentinel

    @pytest.mark.asyncio
    async def test_custom_id_used(self):
        opaque = object()
        pool = _make_pool(fetchrow_results=[opaque])
        db = _make_db(pool)

        with patch(f"{_CONVERTER}.to_user_response", return_value=opaque):
            await db.create_user({"id": "custom-id", "email": "bob@example.com"})

        # Verify that fetchrow was called (insert ran)
        async with pool.acquire() as conn:
            conn.fetchrow.assert_awaited()


# ---------------------------------------------------------------------------
# get_or_create_oauth_user — three code paths
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestGetOrCreateOAuthUser:
    @pytest.mark.asyncio
    async def test_path1_existing_oauth_returns_user(self):
        """Path 1: oauth_accounts row exists → fetch and return the linked user."""
        # oauth_row[user_id] IS read by production (users_db.py:170),
        # so it needs the strict row-faker. user_row only flows to the
        # patched converter — opaque sentinel is enough.
        oauth_row = _make_row(user_id="existing-user-uuid")
        pool = _make_pool(fetchrow_results=[oauth_row, object()])
        db = _make_db(pool)

        sentinel = object()
        with patch(f"{_CONVERTER}.to_user_response", return_value=sentinel):
            result = await db.get_or_create_oauth_user(
                provider="github",
                provider_user_id="gh-12345",
                provider_data={"email": "alice@example.com"},
            )

        assert result is sentinel

    @pytest.mark.asyncio
    async def test_path2_email_exists_links_oauth(self):
        """Path 2: no oauth row, but email matches existing user → link OAuth."""
        # existing_user[id] IS read by production (users_db.py:191) before
        # being handed to the patched converter, so it needs the
        # strict row-faker.
        no_oauth = None
        existing_user = _make_row(id="existing-user-uuid")
        pool = _make_pool(fetchrow_results=[no_oauth, existing_user])
        db = _make_db(pool)

        sentinel = object()
        with patch(f"{_CONVERTER}.to_user_response", return_value=sentinel):
            result = await db.get_or_create_oauth_user(
                provider="github",
                provider_user_id="gh-99999",
                provider_data={"email": "alice@example.com", "username": "alice"},
            )

        assert result is sentinel
        # execute() should have been called to INSERT into oauth_accounts
        async with pool.acquire() as conn:
            conn.execute.assert_awaited()

    @pytest.mark.asyncio
    async def test_path3_new_user_created(self):
        """Path 3: no oauth row, no email match → create new user and OAuth account."""
        no_oauth = None
        no_existing_user = None
        # Patch uuid4 so we know the generated user_id in advance.
        # winner_recheck[user_id] IS read by production (users_db.py:261)
        # for the concurrent-create check; new_user_row only flows to
        # the patched converter and stays opaque.
        _fixed_uuid = "brand-new-uuid"
        winner_recheck = _make_row(user_id=_fixed_uuid)
        pool = _make_pool(
            fetchrow_results=[no_oauth, no_existing_user, object(), winner_recheck]
        )
        db = _make_db(pool)

        sentinel = object()
        with (
            patch(f"{_CONVERTER}.to_user_response", return_value=sentinel),
            patch("services.users_db.uuid4", return_value=_fixed_uuid),
        ):
            result = await db.get_or_create_oauth_user(
                provider="google",
                provider_user_id="google-xyz",
                provider_data={"email": "newuser@example.com", "username": "newuser"},
            )

        assert result is sentinel

    @pytest.mark.asyncio
    async def test_path3_no_email_uses_fallback_username(self):
        """If provider_data has no email, username fallback 'user' is used.

        With no email, production code skips the existing-user-by-email
        lookup (``users_db.py:183``), so only THREE fetchrow calls happen:
        oauth check → INSERT user RETURNING → winner-row recheck.
        Previously this test seeded 4 rows with the wrong shapes and
        silently passed on the concurrent-winner code path because the
        non-strict ``__getitem__`` returned ``None`` instead of failing —
        see GH#337.
        """
        no_oauth = None
        _fixed_uuid = "new-uuid"
        # winner_recheck[user_id] is read by production; new_user_row
        # only flows to the patched converter.
        winner_recheck = _make_row(user_id=_fixed_uuid)
        pool = _make_pool(
            fetchrow_results=[no_oauth, object(), winner_recheck]
        )
        db = _make_db(pool)

        with (
            patch(f"{_CONVERTER}.to_user_response", return_value=MagicMock()),
            patch("services.users_db.uuid4", return_value=_fixed_uuid),
        ):
            # Should not raise even with no email
            await db.get_or_create_oauth_user(
                provider="github",
                provider_user_id="gh-no-email",
                provider_data={},  # No email
            )


# ---------------------------------------------------------------------------
# get_or_create_oauth_user — race condition (issue #767)
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestGetOrCreateOAuthUserRace:
    """Verify the REPEATABLE READ + ON CONFLICT race-condition fix (issue #767)."""

    @pytest.mark.asyncio
    async def test_transaction_is_used_in_path3(self):
        """The connection must use a transaction wrapper for the create path."""
        _fixed_uuid = "race-uuid"
        no_oauth = None
        no_existing_user = None
        # winner_recheck[user_id] is read by production; new_user_row
        # only flows to the patched converter.
        winner_recheck = _make_row(user_id=_fixed_uuid)
        pool = _make_pool(
            fetchrow_results=[no_oauth, no_existing_user, object(), winner_recheck]
        )

        # Spy on the transaction context manager
        transaction_entered = []

        @asynccontextmanager
        async def _spy_transaction(**_kwargs):
            transaction_entered.append(True)
            yield

        # Inject spy into the conn object that _acquire yields
        original_acquire = pool.acquire

        @asynccontextmanager
        async def _spy_acquire():
            async with original_acquire() as conn:
                conn.transaction = _spy_transaction
                yield conn

        pool.acquire = _spy_acquire
        db = _make_db(pool)

        with (
            patch(f"{_CONVERTER}.to_user_response", return_value=MagicMock()),
            patch("services.users_db.uuid4", return_value=_fixed_uuid),
        ):
            await db.get_or_create_oauth_user(
                provider="github",
                provider_user_id="gh-race-test",
                provider_data={"email": "race@example.com"},
            )

        assert transaction_entered, "conn.transaction() must be entered in path 3"

    @pytest.mark.asyncio
    async def test_concurrent_winner_returns_winner_user(self):
        """If a concurrent coroutine won the insert race, return the winner's user."""
        _my_uuid = "my-uuid"
        _winner_uuid = "winner-uuid"
        no_oauth = None
        no_existing_user = None
        # Only winner_recheck[user_id] is read by production. The other
        # two fetchrow returns flow into the patched converter (or get
        # discarded entirely) and stay opaque.
        winner_recheck = _make_row(user_id=_winner_uuid)
        pool = _make_pool(
            fetchrow_results=[
                no_oauth,
                no_existing_user,
                object(),
                winner_recheck,
                object(),
            ]
        )
        db = _make_db(pool)

        winner_sentinel = object()
        with (
            patch(f"{_CONVERTER}.to_user_response", return_value=winner_sentinel),
            patch("services.users_db.uuid4", return_value=_my_uuid),
        ):
            result = await db.get_or_create_oauth_user(
                provider="github",
                provider_user_id="gh-concurrent",
                provider_data={"email": "race@example.com"},
            )

        assert result is winner_sentinel


# ---------------------------------------------------------------------------
# get_oauth_accounts
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestGetOAuthAccounts:
    @pytest.mark.asyncio
    async def test_returns_oauth_account_responses(self):
        # Opaque rows — the test asserts only the count and that each
        # element passed through the patched converter.
        pool = _make_pool(fetch_result=[object(), object()])
        db = _make_db(pool)

        sentinel = object()
        with patch(f"{_CONVERTER}.to_oauth_account_response", return_value=sentinel):
            result = await db.get_oauth_accounts("user-uuid")

        assert len(result) == 2
        assert all(r is sentinel for r in result)

    @pytest.mark.asyncio
    async def test_no_accounts_returns_empty_list(self):
        pool = _make_pool(fetch_result=[])
        db = _make_db(pool)

        result = await db.get_oauth_accounts("user-uuid")
        assert result == []


# ---------------------------------------------------------------------------
# unlink_oauth_account
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestUnlinkOAuthAccount:
    @pytest.mark.asyncio
    async def test_success_returns_true(self):
        pool = _make_pool(execute_result="DELETE 1")
        db = _make_db(pool)
        result = await db.unlink_oauth_account("user-uuid", "github")
        assert result is True

    @pytest.mark.asyncio
    async def test_nothing_deleted_returns_false(self):
        """When no row matches (DELETE 0), returns False."""
        pool = _make_pool(execute_result="DELETE 0")
        db = _make_db(pool)
        result = await db.unlink_oauth_account("user-uuid", "github")
        assert result is False

    @pytest.mark.asyncio
    async def test_db_error_returns_false(self):
        pool = _make_pool()
        async with pool.acquire() as conn:
            conn.execute = AsyncMock(side_effect=RuntimeError("DB down"))
        db = _make_db(pool)
        result = await db.unlink_oauth_account("user-uuid", "github")
        assert result is False
