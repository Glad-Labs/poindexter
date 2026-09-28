"""Integration: migration 20260928_141858 clears exactly the admin-lookup stamps.

Until 2026-09-28 ``GET/POST/PUT /api/settings/{key}`` recorded a read, so the
usual ``poindexter settings set`` + ``poindexter settings get`` stamped
``app_settings.last_read_at`` within a minute of the edit, and the zero-reader
probe (which lists only never-stamped keys) never reported the key again. The
migration resets stamps with that signature: at or within 5 minutes after the
row's last value edit, and unmoved for a day.

Runs the migration's own SQL against real Postgres inside the rolled-back
``test_txn``. ``now()`` is frozen for the transaction, which is why the
"recent" case is seeded relative to ``NOW()`` in SQL rather than from Python's
clock.
"""

from __future__ import annotations

import datetime as dt
import importlib.util
from pathlib import Path

import pytest

pytestmark = [
    pytest.mark.integration_db,
    pytest.mark.asyncio(loop_scope="session"),
]

_MIGRATION = (
    Path(__file__).resolve().parents[2]
    / "poindexter"
    / "services"
    / "migrations"
    / "20260928_141858_clear_last_read_at_stamps_left_by_settings_admin_lookups.py"
)
_EDIT = dt.datetime(2026, 7, 14, 17, 40, 50, tzinfo=dt.UTC)
_PREFIX = "clear_stamp_probe_"


def _load():
    spec = importlib.util.spec_from_file_location("clear_admin_lookup_stamps_int", _MIGRATION)
    module = importlib.util.module_from_spec(spec)  # type: ignore[arg-type]
    spec.loader.exec_module(module)  # type: ignore[union-attr]
    return module


async def _seed(conn, name: str, updated_at, last_read_at) -> None:
    await conn.execute(
        """
        INSERT INTO app_settings (key, value, category, updated_at, last_read_at)
        VALUES ($1, 'v', 'testing', $2, $3)
        """,
        _PREFIX + name,
        updated_at,
        last_read_at,
    )


async def test_clears_only_stamps_that_sit_just_after_an_edit(test_txn) -> None:
    mig = _load()
    await _seed(test_txn, "looked_up_after_edit", _EDIT, _EDIT + dt.timedelta(seconds=19))
    await _seed(test_txn, "at_the_window_edge", _EDIT, _EDIT + dt.timedelta(minutes=5))
    await _seed(test_txn, "read_well_after_edit", _EDIT, _EDIT + dt.timedelta(minutes=10))
    await _seed(test_txn, "read_before_the_edit", _EDIT, _EDIT - dt.timedelta(minutes=1))
    await _seed(test_txn, "never_read", _EDIT, None)
    # Looked up an hour ago: can't yet tell it from a real read, so it stays.
    await test_txn.execute(
        """
        INSERT INTO app_settings (key, value, category, updated_at, last_read_at)
        VALUES ($1, 'v', 'testing', NOW() - INTERVAL '1 hour',
                NOW() - INTERVAL '1 hour' + INTERVAL '30 seconds')
        """,
        _PREFIX + "looked_up_an_hour_ago",
    )

    rows = await test_txn.fetch(mig.CLEAR_SQL, mig.EDIT_WINDOW_MINUTES, mig.UNMOVED_FOR_HOURS)

    cleared = sorted(r["key"] for r in rows if r["key"].startswith(_PREFIX))
    assert cleared == [_PREFIX + "at_the_window_edge", _PREFIX + "looked_up_after_edit"]

    after = {
        r["key"]: r["last_read_at"]
        for r in await test_txn.fetch(
            "SELECT key, last_read_at FROM app_settings WHERE key LIKE $1", _PREFIX + "%"
        )
    }
    assert after[_PREFIX + "looked_up_after_edit"] is None
    assert after[_PREFIX + "read_well_after_edit"] == _EDIT + dt.timedelta(minutes=10)
    assert after[_PREFIX + "read_before_the_edit"] == _EDIT - dt.timedelta(minutes=1)
    assert after[_PREFIX + "never_read"] is None
    assert after[_PREFIX + "looked_up_an_hour_ago"] is not None


async def test_leaves_every_value_untouched(test_txn) -> None:
    mig = _load()
    await _seed(test_txn, "value_kept", _EDIT, _EDIT + dt.timedelta(seconds=30))

    await test_txn.fetch(mig.CLEAR_SQL, mig.EDIT_WINDOW_MINUTES, mig.UNMOVED_FOR_HOURS)

    row = await test_txn.fetchrow(
        "SELECT value, updated_at, last_read_at FROM app_settings WHERE key = $1",
        _PREFIX + "value_kept",
    )
    assert row["value"] == "v"
    assert row["updated_at"] == _EDIT  # the value trigger did not fire
    assert row["last_read_at"] is None
