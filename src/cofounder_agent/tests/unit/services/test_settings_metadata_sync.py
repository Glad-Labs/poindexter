"""The seeder's lifecycle-metadata pass must survive one bad registry entry.

Until 2026-09-12 a single METADATA entry whose ``value_type`` the DB CHECK
rejects aborted the whole UPDATE loop inside a silent ``except``, so 192 rows
(``wan_ip_probe_url`` among them) never received their ``owner`` -- and the
brain's operator-URL probe, which reads ``app_settings.owner``, could not tell
a probe target from an operator surface.
"""
from __future__ import annotations

import logging
from unittest.mock import AsyncMock, MagicMock

import pytest

from poindexter.services import settings_defaults as sd


def _pool(execute_side_effect):
    conn = MagicMock()
    conn.execute = AsyncMock(side_effect=execute_side_effect)
    conn.fetch = AsyncMock(return_value=[])
    conn.executemany = AsyncMock(return_value=None)
    ctx = MagicMock()
    ctx.__aenter__ = AsyncMock(return_value=conn)
    ctx.__aexit__ = AsyncMock(return_value=False)
    pool = MagicMock()
    pool.acquire = MagicMock(return_value=ctx)
    return pool, conn


def _updates(conn):
    return [c for c in conn.execute.await_args_list if c.args and "UPDATE app_settings SET" in c.args[0]]


@pytest.mark.unit
@pytest.mark.asyncio
async def test_one_rejected_entry_does_not_abort_the_sync(caplog):
    bad_key = next(iter(sd.METADATA))

    async def execute(sql, *args):
        if "UPDATE app_settings SET" in sql and args and args[0] == bad_key:
            raise RuntimeError(
                'new row for relation "app_settings" violates check constraint '
                '"app_settings_value_type_check"'
            )
        return "INSERT 0 0"

    pool, conn = _pool(execute)
    with caplog.at_level(logging.WARNING, logger=sd.__name__):
        await sd.seed_all_defaults(pool)
    assert len(_updates(conn)) == len(sd.METADATA), "every registry entry is still attempted"
    assert any("rejected 1 of" in r.getMessage() and bad_key in r.getMessage() for r in caplog.records)


@pytest.mark.unit
@pytest.mark.asyncio
async def test_missing_lifecycle_columns_defer_quietly_once(caplog):
    async def execute(sql, *args):
        if "UPDATE app_settings SET" in sql:
            raise RuntimeError('column "owner" of relation "app_settings" does not exist')
        return "INSERT 0 0"

    pool, conn = _pool(execute)
    with caplog.at_level(logging.INFO, logger=sd.__name__):
        await sd.seed_all_defaults(pool)
    assert len(_updates(conn)) == 1, "an absent column stops the pass after the first attempt"
    assert not [r for r in caplog.records if r.levelno >= logging.WARNING]
    assert any("deferred" in r.getMessage() for r in caplog.records)
