"""HelloTap ships disabled — a sample must not write into a real corpus.

``HelloTap`` yields one static Document that lands as
``embeddings.source_table='samples'`` and never changes. Left enabled it is
permanently the stalest source on the corpus-freshness panel
(poindexter#989) — a fake red that teaches an operator to ignore the panel —
and it violates ``feedback_no_dummy_data``.

The tap stays registered (it is the reference implementation third-party Tap
authors read); only its default `enabled` flips.
"""

from __future__ import annotations

import importlib.util
import json
from pathlib import Path
from typing import Any

import pytest

from services.settings_defaults import DEFAULTS

_MIGRATION_FILE = (
    Path(__file__).resolve().parents[3]
    / "services"
    / "migrations"
    / "20260807_183202_drop_the_hello_sample_tap_demo_embedding_row.py"
)


def _load_migration():
    spec = importlib.util.spec_from_file_location(_MIGRATION_FILE.stem, _MIGRATION_FILE)
    module = importlib.util.module_from_spec(spec)  # type: ignore[arg-type]
    spec.loader.exec_module(module)  # type: ignore[union-attr]
    return module


class _FakeConn:
    def __init__(self, deleted: int = 1) -> None:
        self._deleted = deleted
        self.calls: list[tuple[Any, ...]] = []

    async def fetchval(self, _sql: str, *args: Any) -> int:
        self.calls.append(args)
        return self._deleted


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


class TestDefault:
    def test_hello_tap_is_disabled_by_default(self):
        raw = DEFAULTS.get("plugin.tap.hello")
        assert raw is not None, "plugin.tap.hello must be seeded, not left implicit"
        assert json.loads(raw)["enabled"] is False

    def test_the_sample_tap_is_still_registered(self):
        """Disabling is not deleting — authors still need the reference."""
        from plugins.registry import get_core_samples

        names = {t.name for t in get_core_samples().get("taps", [])}
        assert "hello" in names

    def test_the_config_key_matches_the_tap_name(self):
        """`plugin.tap.<name>` — a mismatch would silently do nothing."""
        from plugins.samples.hello_tap import HelloTap

        assert f"plugin.tap.{HelloTap().name}" in DEFAULTS


class TestMigration:
    @pytest.mark.asyncio
    async def test_deletes_only_the_sample_row(self):
        conn = _FakeConn()
        await _load_migration().up(_FakePool(conn))

        assert len(conn.calls) == 1
        # Scoped to the sample's own source_id, so an operator's own rows
        # under source_table='samples' survive.
        assert conn.calls[0][0] == "samples/hello/greeting"

    @pytest.mark.asyncio
    async def test_rerun_deleting_nothing_is_fine(self):
        conn = _FakeConn(deleted=0)
        await _load_migration().up(_FakePool(conn))  # must not raise

    @pytest.mark.asyncio
    async def test_down_is_a_noop(self):
        await _load_migration().down(_FakePool(_FakeConn()))
