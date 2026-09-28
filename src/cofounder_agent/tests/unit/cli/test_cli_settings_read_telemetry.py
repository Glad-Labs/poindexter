"""The CLI stamps the settings a command reads (read telemetry, poindexter#756).

A ``poindexter`` command is a process of its own, and read telemetry is
buffered in process memory, so until 2026-09-28 every read a CLI command made
died with it: a key read only by the CLI looked unused to
``ProbeZeroReaderSettingsJob``. The fix has three parts, each pinned here:

- ``cli_site_config(pool)`` builds every CLI ``SiteConfig`` with the
  process-wide ``settings_read_sink`` as its read recorder. Commands build
  their SiteConfig inside ``run_service`` factories and ``_make_site_config``
  helpers that return before the pool closes, so reads parked on the instance
  would be gone by then. That lifetime case has a test of its own.
- ``close_cli_pool`` stamps the sink through the command's pool before it
  closes it, the one teardown every CLI pool goes through
  (``cli_audit_sink_lint``).
- Telemetry never breaks the command: a failed stamp is logged, the pool
  still closes.

``container_for_cli``'s own flush is covered in ``test_lifecycle.py``.
"""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock, patch

import pytest

from poindexter.cli._bootstrap import cli_site_config, close_cli_pool
from poindexter.cli._dataplane import run_service
from poindexter.services import settings_read_sink


class _Conn:
    def __init__(self, pool: _FakePool) -> None:
        self._pool = pool

    async def fetch(self, sql: str, *args: Any) -> list[dict[str, str]]:
        self._pool.events.append(("fetch", sql, args))
        return []  # no control rows: telemetry on, default restamp window

    async def execute(self, sql: str, *args: Any) -> str:
        if self._pool.closed:
            raise RuntimeError("pool is closing")
        self._pool.events.append(("execute", sql, args))
        return f"UPDATE {len(args[0])}"


class _Acquire:
    def __init__(self, pool: _FakePool) -> None:
        self._pool = pool

    async def __aenter__(self) -> _Conn:
        return _Conn(self._pool)

    async def __aexit__(self, *exc: object) -> bool:
        return False


class _FakePool:
    """An asyncpg-pool stand-in recording the order of stamps and the close."""

    def __init__(self) -> None:
        self.closed = False
        self.events: list[tuple] = []
        self.fetch = AsyncMock(return_value=[])  # SiteConfig.load's query

    def acquire(self) -> _Acquire:
        return _Acquire(self)

    async def close(self) -> None:
        self.closed = True
        self.events.append(("close",))

    def stamped_keys(self) -> list[str]:
        return [key for e in self.events if e[0] == "execute" for key in e[2][0]]


@pytest.mark.unit
class TestCliSiteConfig:
    def test_reads_land_in_the_process_wide_sink(self):
        pool = _FakePool()
        sc = cli_site_config(pool)

        sc.get("pro_delivery_enabled")
        sc.get_int("schedule_batch_default_interval_minutes", 60)

        assert sorted(settings_read_sink.drain_read_keys()) == [
            "pro_delivery_enabled",
            "schedule_batch_default_interval_minutes",
        ]
        assert sc.drain_read_keys() == []

    def test_is_bound_to_the_pool_for_secrets(self):
        pool = _FakePool()
        assert cli_site_config(pool)._pool is pool  # noqa: SLF001


@pytest.mark.unit
class TestCloseCliPoolStampsReads:
    async def test_stamps_the_commands_reads_before_closing_the_pool(self):
        pool = _FakePool()
        cli_site_config(pool).get("community_default_writer_model")

        await close_cli_pool(pool)

        assert pool.stamped_keys() == ["community_default_writer_model"]
        kinds = [e[0] for e in pool.events]
        assert kinds.index("execute") < kinds.index("close"), kinds
        assert pool.closed

    def test_a_siteconfig_gone_before_the_pool_closes_still_has_its_reads_stamped(self):
        """The lifetime case that rules out keeping reads on the instance:
        ``run_service`` closes the pool after the factory returns, and by then
        the SiteConfig the factory built is unreachable."""
        pool = _FakePool()

        async def factory(p: Any) -> str:
            sc = cli_site_config(p)
            return sc.get("tap_run_timeout_seconds", "300")

        with patch("poindexter.cli._dataplane.open_cli_pool", AsyncMock(return_value=pool)):
            assert run_service(factory) == "300"

        assert pool.stamped_keys() == ["tap_run_timeout_seconds"]
        assert pool.closed

    async def test_a_command_that_read_nothing_writes_nothing(self):
        pool = _FakePool()

        await close_cli_pool(pool)

        assert pool.events == [("close",)]

    async def test_a_failed_stamp_never_stops_the_pool_closing(self, caplog):
        pool = _FakePool()
        settings_read_sink.record_read("some_key")

        async def _refuse(self: _Conn, sql: str, *args: Any) -> str:
            raise PermissionError("permission denied for table app_settings")

        with patch.object(_Conn, "execute", _refuse), caplog.at_level("WARNING"):
            await close_cli_pool(pool)

        assert pool.closed
        assert any("last_read_at UPDATE failed" in r.getMessage() for r in caplog.records)
