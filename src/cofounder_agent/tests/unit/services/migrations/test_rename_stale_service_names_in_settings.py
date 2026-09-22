"""Regression guard for the stale-service-name repair.

``game_mode_parked_services`` said ``stable-audio`` (the service is
``stable-audio-server``) and ``compose_drift_on_demand_services`` said
``sdxl-server`` (renamed to ``image-gen-server``). Neither name matched a
compose service, so both entries silently did nothing.
"""

from __future__ import annotations

import importlib.util
from pathlib import Path
from typing import Any

import pytest

_MIGRATION_FILE = (
    Path(__file__).resolve().parents[4]
    / "poindexter"
    / "services"
    / "migrations"
    / "20260921_190000_rename_stale_service_names_in_game_mode_and_drift_settings.py"
)


def _load_migration():
    spec = importlib.util.spec_from_file_location(_MIGRATION_FILE.stem, _MIGRATION_FILE)
    module = importlib.util.module_from_spec(spec)  # type: ignore[arg-type]
    spec.loader.exec_module(module)  # type: ignore[union-attr]
    return module


class _FakeConn:
    def __init__(self, rows: dict[str, str]) -> None:
        self.rows = dict(rows)
        self.updates: list[tuple[Any, ...]] = []

    async def fetchval(self, _sql: str, key: str) -> str | None:
        return self.rows.get(key)

    async def execute(self, _sql: str, value: str, key: str) -> str:
        self.updates.append((key, value))
        self.rows[key] = value
        return "UPDATE 1"


class _FakePool:
    def __init__(self, conn: _FakeConn) -> None:
        self._conn = conn

    def acquire(self):
        conn = self._conn

        class _Ctx:
            async def __aenter__(self):
                return conn

            async def __aexit__(self, *exc):
                return False

        return _Ctx()


@pytest.mark.asyncio
async def test_renames_both_stale_names_and_keeps_everything_else():
    conn = _FakeConn(
        {
            "game_mode_parked_services": "speaches,chatterbox,stable-audio,image-gen-server,wan-server",
            "compose_drift_on_demand_services": "wan-server,sdxl-server",
        }
    )
    await _load_migration().up(_FakePool(conn))
    assert conn.rows["game_mode_parked_services"] == (
        "speaches,chatterbox,stable-audio-server,image-gen-server,wan-server"
    )
    assert conn.rows["compose_drift_on_demand_services"] == "wan-server,image-gen-server"


@pytest.mark.asyncio
async def test_is_idempotent_and_skips_rows_without_the_stale_name():
    conn = _FakeConn(
        {
            "game_mode_parked_services": "speaches,stable-audio-server",
            "compose_drift_on_demand_services": "wan-server,image-gen-server",
        }
    )
    await _load_migration().up(_FakePool(conn))
    assert conn.updates == []


@pytest.mark.asyncio
async def test_rename_does_not_duplicate_an_entry_already_present():
    conn = _FakeConn({"compose_drift_on_demand_services": "image-gen-server,sdxl-server"})
    await _load_migration().up(_FakePool(conn))
    assert conn.rows["compose_drift_on_demand_services"] == "image-gen-server"


@pytest.mark.asyncio
async def test_missing_rows_are_a_no_op():
    conn = _FakeConn({})
    await _load_migration().up(_FakePool(conn))
    assert conn.updates == []


def test_only_whole_entries_are_renamed():
    mod = _load_migration()
    assert (
        mod._rename_entries(
            "stable-audio-open,stable-audio", {"stable-audio": "stable-audio-server"}
        )
        == "stable-audio-open,stable-audio-server"
    )
