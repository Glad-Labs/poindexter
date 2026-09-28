"""Migration 20260928_141858 clears the last_read_at stamps settings lookups left.

Until 2026-09-28, ``GET/POST/PUT /api/settings/{key}`` recorded the key as
read, so ``poindexter settings get <key>`` after a ``settings set`` stamped it,
and ``ProbeZeroReaderSettingsJob`` (which lists only never-stamped keys) never
reported it again. The migration resets the stamps with that signature: within
5 minutes after the last value edit, unmoved for a day. The SQL itself is
exercised against real Postgres in
``tests/integration_db/test_clear_admin_lookup_stamps_migration.py``; this pins
the runner contract.
"""

from __future__ import annotations

import importlib.util
import logging
from contextlib import asynccontextmanager
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest

_MIGRATION = (
    Path(__file__).resolve().parents[4]
    / "poindexter"
    / "services"
    / "migrations"
    / "20260928_141858_clear_last_read_at_stamps_left_by_settings_admin_lookups.py"
)


def _load():
    spec = importlib.util.spec_from_file_location("clear_admin_lookup_stamps", _MIGRATION)
    module = importlib.util.module_from_spec(spec)  # type: ignore[arg-type]
    spec.loader.exec_module(module)  # type: ignore[union-attr]
    return module


def _pool(rows: list[dict[str, str]]):
    conn = MagicMock()
    conn.fetch = AsyncMock(return_value=rows)
    pool = MagicMock()

    @asynccontextmanager
    async def _acquire():
        yield conn

    pool.acquire = _acquire
    return pool, conn


@pytest.mark.unit
class TestClearAdminLookupStamps:
    async def test_up_runs_the_clear_with_its_windows_and_logs_the_keys(self, caplog):
        mig = _load()
        pool, conn = _pool([{"key": "ragas_enabled"}, {"key": "compose_drift_on_demand_services"}])

        with caplog.at_level(logging.INFO):
            await mig.up(pool)

        sql, window_minutes, unmoved_hours = conn.fetch.await_args.args
        assert sql is mig.CLEAR_SQL
        assert (window_minutes, unmoved_hours) == (5, 24)
        message = " ".join(r.getMessage() for r in caplog.records)
        assert "cleared 2 stamp(s)" in message
        assert "compose_drift_on_demand_services, ragas_enabled" in message

    async def test_up_is_a_quiet_no_op_on_a_fresh_install(self, caplog):
        mig = _load()
        pool, _ = _pool([])

        with caplog.at_level(logging.INFO):
            await mig.up(pool)

        assert any("cleared 0 stamp(s)" in r.getMessage() for r in caplog.records)

    def test_the_sql_only_ever_clears_last_read_at(self):
        """A telemetry reset: it must never touch a setting's value, and it
        clears nothing but stamps sitting just after an edit."""
        sql = " ".join(_load().CLEAR_SQL.split())
        assert sql.startswith("UPDATE app_settings SET last_read_at = NULL WHERE")
        assert "value" not in sql.split("WHERE")[0].replace("last_read_at", "")
        assert "last_read_at >= updated_at" in sql
        assert "last_read_at <= updated_at + ($1 * INTERVAL '1 minute')" in sql
        assert "last_read_at < NOW() - ($2 * INTERVAL '1 hour')" in sql

    async def test_down_is_a_no_op(self):
        mig = _load()
        pool = MagicMock()

        await mig.down(pool)

        pool.acquire.assert_not_called()
