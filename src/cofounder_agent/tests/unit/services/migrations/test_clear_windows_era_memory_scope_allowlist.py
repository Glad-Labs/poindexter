"""Regression guard for the Windows-era memory-scope-allowlist repair.

``plugin.tap.memory.config.memory_scope_allowlist`` was a Windows Junction
dedup guard (``C--Users-alice`` ⇄ ``C--Users-alice-myproject``). The
Pop!_OS migration re-keyed Claude project scopes to the Linux checkout
path, stranding the value on a scope that no longer exists — so it
filtered out every scope on disk and the memory tap ingested zero files
for 17 days while still reporting a clean run.

The migration clears only ``C--``-prefixed entries. These tests pin the
three behaviours that matter: the repair fires on a stranded value, a
deliberate present-day allowlist survives, and installs without the row
(every fresh one — ``plugin.tap.memory`` is not seeded anywhere) are a
no-op rather than an error.
"""

from __future__ import annotations

import importlib.util
import json
from pathlib import Path
from typing import Any

import pytest

_MIGRATION_FILE = (
    Path(__file__).resolve().parents[4]
    / "services"
    / "migrations"
    / "20260806_053312_clear_windows_era_memory_scope_allowlist_stranded_by_pop_os_migration.py"
)


def _load_migration():
    """Import the timestamp-prefixed migration the way the real runner does —
    its filename isn't a valid Python identifier."""
    spec = importlib.util.spec_from_file_location(_MIGRATION_FILE.stem, _MIGRATION_FILE)
    module = importlib.util.module_from_spec(spec)  # type: ignore[arg-type]
    spec.loader.exec_module(module)  # type: ignore[union-attr]
    return module


class _FakeConn:
    """Minimal asyncpg-shaped connection recording UPDATE calls."""

    def __init__(self, value: str | None) -> None:
        self._value = value
        self.updates: list[tuple[Any, ...]] = []

    async def fetchval(self, _sql: str, *args: Any) -> str | None:
        return self._value

    async def execute(self, _sql: str, *args: Any) -> str:
        self.updates.append(args)
        return "UPDATE 1"


class _FakeAcquire:
    def __init__(self, conn: _FakeConn) -> None:
        self._conn = conn

    async def __aenter__(self) -> _FakeConn:
        return self._conn

    async def __aexit__(self, *_exc: Any) -> bool:
        return False


class _FakePool:
    def __init__(self, conn: _FakeConn) -> None:
        self._conn = conn

    def acquire(self) -> _FakeAcquire:
        return _FakeAcquire(self._conn)


def _setting(allowlist: str) -> str:
    return json.dumps(
        {
            "enabled": True,
            "interval_seconds": 3600,
            "config": {
                "openclaw_memory_dir": "__skip__",
                "shared_context_dir": "__skip__",
                "memory_scope_allowlist": allowlist,
            },
        }
    )


class TestStripWindowsScopes:
    """The pure helper — the actual filtering rule."""

    @pytest.mark.parametrize(
        ("given", "expected"),
        [
            ("C--Users-alice", ""),
            ("c--users-alice", ""),  # case-insensitive
            ("C--Users-a,C--Users-b", ""),
            ("C--Users-alice,-home-alice-project", "-home-alice-project"),
            ("-home-alice-project", "-home-alice-project"),
            ("", ""),
        ],
    )
    def test_filters_only_windows_scopes(self, given: str, expected: str):
        mod = _load_migration()
        assert mod._strip_windows_scopes(given) == expected


class TestUp:
    @pytest.mark.asyncio
    async def test_clears_stranded_windows_allowlist(self):
        """The exact prod value that blinded the tap for 17 days."""
        conn = _FakeConn(_setting("C--Users-alice"))
        mod = _load_migration()

        await mod.up(_FakePool(conn))

        assert len(conn.updates) == 1
        written = json.loads(conn.updates[0][0])
        assert written["config"]["memory_scope_allowlist"] == ""
        # Unrelated config must survive the rewrite.
        assert written["config"]["openclaw_memory_dir"] == "__skip__"
        assert written["enabled"] is True

    @pytest.mark.asyncio
    async def test_leaves_present_day_allowlist_untouched(self):
        """A deliberate Linux-scope allowlist is real config, not debris."""
        conn = _FakeConn(_setting("-home-alice-project"))
        mod = _load_migration()

        await mod.up(_FakePool(conn))

        assert conn.updates == []

    @pytest.mark.asyncio
    async def test_missing_row_is_a_noop(self):
        """Every fresh install — plugin.tap.memory is not seeded anywhere."""
        conn = _FakeConn(None)
        mod = _load_migration()

        await mod.up(_FakePool(conn))

        assert conn.updates == []

    @pytest.mark.asyncio
    async def test_malformed_json_is_left_alone(self):
        """Fail loud, don't guess — rewriting could destroy real config."""
        conn = _FakeConn("not json{")
        mod = _load_migration()

        await mod.up(_FakePool(conn))

        assert conn.updates == []

    @pytest.mark.asyncio
    async def test_rerun_is_idempotent(self):
        """Second apply finds nothing Windows-era left and writes nothing."""
        conn = _FakeConn(_setting("C--Users-alice"))
        mod = _load_migration()

        await mod.up(_FakePool(conn))
        conn._value = conn.updates[0][0]  # feed the repaired value back
        await mod.up(_FakePool(conn))

        assert len(conn.updates) == 1
