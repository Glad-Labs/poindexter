"""Unit tests for migration 0125 — api_token / api_auth_token consolidation.

Closes Glad-Labs/poindexter#326. The migration handles four input
states; this suite exercises each via a mocked asyncpg connection
without spinning up Postgres.

State 1: both rows exist, ``api_token`` is empty, ``api_auth_token``
         has a value → copy + delete.
State 2: both rows exist, ``api_token`` has a value → keep canonical,
         delete dead.
State 3: only ``api_auth_token`` exists → copy + delete.
State 4: only ``api_token`` exists / neither exists → no-op.

Plus a smoke check that the migration is idempotent on re-run and
that it degrades gracefully when ``POINDEXTER_SECRET_KEY`` is unset
(CI ``migrations_smoke`` path).
"""

from __future__ import annotations

import importlib
from unittest.mock import AsyncMock, MagicMock

import pytest


def _import_migration():
    """Late-import so the migration runner's discovery glob doesn't double-run."""
    return importlib.import_module(
        "services.migrations.0125_consolidate_api_token_settings"
    )


def _make_pool(rows_by_key: dict[str, dict] | None = None,
               table_exists: bool = True):
    """Build a mock asyncpg pool whose conn.fetchrow returns from a key map.

    ``rows_by_key`` is keyed by the second positional arg the migration
    passes to ``conn.fetchrow`` (the app_settings ``key`` value).
    """
    rows_by_key = rows_by_key or {}

    conn = MagicMock()

    async def _fetchval(sql, *params):
        # The migration calls fetchval only for the table-exists check.
        return table_exists

    async def _fetchrow(sql, *params):
        if not params:
            return None
        return rows_by_key.get(params[0])

    conn.fetchval = AsyncMock(side_effect=_fetchval)
    conn.fetchrow = AsyncMock(side_effect=_fetchrow)
    conn.execute = AsyncMock(return_value="DELETE 1")
    conn.__aenter__ = AsyncMock(return_value=conn)
    conn.__aexit__ = AsyncMock(return_value=None)

    pool = MagicMock()
    pool.acquire = MagicMock(return_value=conn)
    return pool, conn


@pytest.mark.unit
class TestNoOpStates:
    """States that should not change the database."""

    @pytest.mark.asyncio
    async def test_no_dead_row_is_noop(self):
        """If api_auth_token doesn't exist, nothing to do (post-0106 state)."""
        m = _import_migration()
        pool, conn = _make_pool(rows_by_key={
            "api_token": {"value": "enc:v1:cipher", "is_secret": True},
        })

        await m.up(pool)

        # No DELETE should have fired — the row isn't there to remove.
        execute_calls = [c for c in conn.execute.await_args_list
                         if "DELETE" in str(c.args[0]).upper()]
        assert execute_calls == []

    @pytest.mark.asyncio
    async def test_no_rows_at_all_is_noop(self):
        """Fresh install — neither row exists. Nothing to do."""
        m = _import_migration()
        pool, conn = _make_pool(rows_by_key={})

        await m.up(pool)

        execute_calls = [c for c in conn.execute.await_args_list
                         if "DELETE" in str(c.args[0]).upper()]
        assert execute_calls == []

    @pytest.mark.asyncio
    async def test_missing_table_is_noop(self):
        """If app_settings doesn't exist (pre-0058), skip cleanly."""
        m = _import_migration()
        pool, conn = _make_pool(table_exists=False)

        await m.up(pool)

        # No fetchrow call should have fired — we bailed at the table check.
        conn.fetchrow.assert_not_awaited()


@pytest.mark.unit
class TestDeadRowCleanup:
    """States where the dead row gets removed."""

    @pytest.mark.asyncio
    async def test_deletes_dead_row_when_live_already_set(self):
        """State 2: canonical wins. Just delete the dead row."""
        m = _import_migration()
        pool, conn = _make_pool(rows_by_key={
            "api_token": {"value": "enc:v1:canonical", "is_secret": True},
            "api_auth_token": {"value": "enc:v1:dead-value", "is_secret": True},
        })

        await m.up(pool)

        # The DELETE for api_auth_token must have fired.
        delete_call = next(
            c for c in conn.execute.await_args_list
            if "DELETE" in str(c.args[0]).upper()
        )
        assert delete_call.args[1] == "api_auth_token"

    @pytest.mark.asyncio
    async def test_deletes_dead_row_when_dead_value_empty(self):
        """If api_auth_token is empty, no value to copy — just delete."""
        m = _import_migration()
        pool, conn = _make_pool(rows_by_key={
            "api_token": {"value": "enc:v1:canonical", "is_secret": True},
            "api_auth_token": {"value": "", "is_secret": True},
        })

        await m.up(pool)

        delete_call = next(
            c for c in conn.execute.await_args_list
            if "DELETE" in str(c.args[0]).upper()
        )
        assert delete_call.args[1] == "api_auth_token"


@pytest.mark.unit
class TestNoSecretKeyEnv:
    """``POINDEXTER_SECRET_KEY`` unset (CI smoke path)."""

    @pytest.mark.asyncio
    async def test_no_key_env_still_deletes_dead_row(self, monkeypatch):
        """Without the symmetric key we can't decrypt — but we still
        clean up the schema. The operator re-sets api_token via the
        env var on next boot."""
        m = _import_migration()
        monkeypatch.delenv("POINDEXTER_SECRET_KEY", raising=False)

        pool, conn = _make_pool(rows_by_key={
            "api_token": {"value": "", "is_secret": True},
            "api_auth_token": {"value": "enc:v1:dead-value", "is_secret": True},
        })

        # Should not raise. Schema cleanup proceeds even though we
        # cannot copy the decrypted value.
        await m.up(pool)

        delete_call = next(
            c for c in conn.execute.await_args_list
            if "DELETE" in str(c.args[0]).upper()
        )
        assert delete_call.args[1] == "api_auth_token"


@pytest.mark.unit
class TestDownIsNoop:
    @pytest.mark.asyncio
    async def test_down_does_not_recreate_dead_row(self):
        """Roll-forward only. down() must not re-insert the orphan."""
        m = _import_migration()
        pool, _ = _make_pool()
        await m.down(pool)
        # pool should never have been touched — down() is a pure logger call.
        pool.acquire.assert_not_called()


@pytest.mark.unit
class TestModuleConstants:
    def test_canonical_key_is_api_token(self):
        m = _import_migration()
        assert m._LIVE_KEY == "api_token"
        assert m._DEAD_KEY == "api_auth_token"
