"""Unit tests for ``services/settings_read_telemetry.py``.

``flush_read_telemetry`` is the drain-and-stamp step of read-telemetry
(Glad-Labs/poindexter#756), shared by every process that flushes: the
worker's ``FlushSettingsReadTelemetryJob`` (once a minute), and each Prefect
content-flow run, CLI command and auto-embed pass (once, as it ends — the
reads buffered in that process are gone afterwards). These tests pin the
helper's contract; the job's mapping onto ``JobResult`` is covered in
``tests/unit/services/jobs/test_flush_settings_read_telemetry.py``, the
flow's end-of-run flush in ``tests/unit/services/flows/``, and the CLI's in
``tests/unit/cli/test_cli_settings_read_telemetry.py``.

Pool mocked (no asyncpg). The SiteConfig is real so the drain semantics are the
production ones.
"""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest

from poindexter.services import settings_read_sink
from poindexter.services.settings_read_telemetry import (
    ReadTelemetryFlush,
    _affected_rows,
    flush_read_telemetry,
)
from poindexter.services.site_config import SiteConfig

_CONTROL_KEYS = {
    "settings_read_telemetry_enabled",
    "settings_read_telemetry_min_restamp_seconds",
}


@pytest.fixture(autouse=True)
def _clean_read_sink():
    """The sink is module-global and other test files leave keys in it; drain
    it around each test so those reads can't leak into these assertions."""
    settings_read_sink.drain_read_keys()
    yield
    settings_read_sink.drain_read_keys()


def _make_pool(
    execute_status: Any = "UPDATE 0",
    execute_error: Exception | None = None,
    control_rows: list[dict[str, str]] | None = None,
    fetch_error: Exception | None = None,
) -> tuple[Any, Any]:
    conn = AsyncMock()
    conn.execute = AsyncMock(return_value=execute_status, side_effect=execute_error)
    # The controls query of a flush with no SiteConfig (the CLI's teardown).
    conn.fetch = AsyncMock(return_value=control_rows or [], side_effect=fetch_error)
    ctx = AsyncMock()
    ctx.__aenter__ = AsyncMock(return_value=conn)
    ctx.__aexit__ = AsyncMock(return_value=False)
    pool = MagicMock()
    pool.acquire = MagicMock(return_value=ctx)
    return pool, conn


def _site_config_with_reads(*keys: str, **settings: str) -> SiteConfig:
    sc = SiteConfig(initial_config=dict(settings))
    for key in keys:
        sc.get(key)
    return sc


@pytest.mark.unit
class TestFlushReadTelemetry:
    async def test_stamps_the_union_of_siteconfig_and_sink_reads(self):
        settings_read_sink.record_read("qa_critic_weight")  # SettingsService path
        sc = _site_config_with_reads("qa_final_score_threshold", "writer_self_review_model")
        pool, conn = _make_pool(execute_status="UPDATE 3")

        flushed = await flush_read_telemetry(pool, sc)

        assert flushed == ReadTelemetryFlush(
            ok=True,
            detail="stamped 3/3 key(s) read since the last flush",
            keys_read=3,
            keys_stamped=3,
        )
        sql, keys, restamp_seconds = conn.execute.await_args.args
        assert "SET last_read_at = NOW()" in sql
        # Sorted, so the UPDATE's key array is deterministic.
        assert keys == [
            "qa_critic_weight",
            "qa_final_score_threshold",
            "writer_self_review_model",
        ]
        assert restamp_seconds == 3600

    async def test_a_key_read_on_both_paths_is_stamped_once(self):
        settings_read_sink.record_read("qa_temperature")
        sc = _site_config_with_reads("qa_temperature")
        pool, conn = _make_pool(execute_status="UPDATE 1")

        flushed = await flush_read_telemetry(pool, sc)

        assert conn.execute.await_args.args[1] == ["qa_temperature"]
        assert flushed.keys_read == 1

    async def test_drains_both_buffers(self):
        settings_read_sink.record_read("qa_temperature")
        sc = _site_config_with_reads("site_url")
        pool, _ = _make_pool(execute_status="UPDATE 2")

        await flush_read_telemetry(pool, sc)

        assert settings_read_sink.drain_read_keys() == []
        # Only the flush's own control reads remain; they happen after the
        # drain, so the next flush stamps them.
        assert set(sc.drain_read_keys()) == _CONTROL_KEYS

    async def test_custom_restamp_window_is_passed_to_the_update(self):
        sc = _site_config_with_reads("site_url", settings_read_telemetry_min_restamp_seconds="60")
        pool, conn = _make_pool(execute_status="UPDATE 1")

        await flush_read_telemetry(pool, sc)

        assert conn.execute.await_args.args[2] == 60

    async def test_nothing_read_writes_nothing(self):
        pool, conn = _make_pool()

        flushed = await flush_read_telemetry(pool, SiteConfig())

        assert flushed == ReadTelemetryFlush(ok=True, detail="no keys read since the last flush")
        conn.execute.assert_not_awaited()

    async def test_disabled_drains_and_discards_without_writing(self):
        settings_read_sink.record_read("qa_temperature")
        sc = _site_config_with_reads("site_url", settings_read_telemetry_enabled="false")
        pool, conn = _make_pool()

        flushed = await flush_read_telemetry(pool, sc)

        assert flushed.ok is True
        assert flushed.keys_read == 2
        assert flushed.keys_stamped == 0
        assert "telemetry disabled" in flushed.detail
        conn.execute.assert_not_awaited()
        # Drained anyway, so neither buffer grows while telemetry is off.
        assert settings_read_sink.drain_read_keys() == []
        assert "site_url" not in sc.drain_read_keys()

    async def test_no_pool_keeps_both_buffers_for_a_later_flush(self):
        settings_read_sink.record_read("qa_temperature")
        sc = _site_config_with_reads("site_url")

        flushed = await flush_read_telemetry(None, sc)

        assert flushed == ReadTelemetryFlush(ok=False, detail="no pool available")
        assert "qa_temperature" in settings_read_sink.drain_read_keys()
        assert "site_url" in sc.drain_read_keys()

    async def test_update_failure_is_reported_not_raised(self, caplog):
        sc = _site_config_with_reads("site_url", "site_name")
        pool, _ = _make_pool(execute_error=OSError("connection reset by peer"))

        with caplog.at_level("WARNING"):
            flushed = await flush_read_telemetry(pool, sc)

        assert flushed.ok is False
        assert flushed.detail.startswith("update failed:")
        assert "connection reset by peer" in flushed.detail
        assert flushed.keys_read == 2
        assert flushed.keys_stamped == 0
        assert any("last_read_at UPDATE failed" in r.message for r in caplog.records)


@pytest.mark.unit
class TestNeverRaises:
    """Callers put the flush in a ``finally`` (the content flow, the CLI's
    ``container_for_cli``, the auto-embed runner) and rely on it never
    replacing their own exception."""

    async def test_a_malformed_site_config_is_reported_not_raised(self, caplog):
        pool, conn = _make_pool()

        with caplog.at_level("WARNING"):
            flushed = await flush_read_telemetry(pool, object())  # no drain_read_keys

        assert flushed.ok is False
        assert flushed.detail.startswith("flush failed:")
        conn.execute.assert_not_awaited()
        assert any("flush failed" in r.message for r in caplog.records)

    async def test_a_pool_that_cannot_hand_out_a_connection_is_reported_not_raised(self):
        settings_read_sink.record_read("site_url")
        pool = MagicMock()
        pool.acquire = MagicMock(side_effect=ConnectionResetError("reset by peer"))

        flushed = await flush_read_telemetry(pool)

        assert flushed.ok is False
        assert "reset by peer" in flushed.detail


@pytest.mark.unit
class TestFlushWithoutASiteConfig:
    """``flush_read_telemetry(pool)``: a process that holds no SiteConfig.

    The CLI's ``close_cli_pool`` flushes this way: every SiteConfig a command
    builds (``cli_site_config``) records into the process-wide sink, so the
    sink is all there is to drain, and the two telemetry controls are read
    from ``app_settings`` on the connection that runs the UPDATE.
    """

    async def test_nothing_read_touches_no_database(self):
        pool, conn = _make_pool()

        flushed = await flush_read_telemetry(pool)

        assert flushed == ReadTelemetryFlush(ok=True, detail="no keys read since the last flush")
        pool.acquire.assert_not_called()

    async def test_stamps_the_sink_with_the_controls_read_from_the_db(self):
        settings_read_sink.record_read("tap_chunk_max_chars")
        settings_read_sink.record_read("pro_delivery_enabled")
        pool, conn = _make_pool(
            execute_status="UPDATE 2",
            control_rows=[{"key": "settings_read_telemetry_min_restamp_seconds", "value": "600"}],
        )

        flushed = await flush_read_telemetry(pool)

        assert flushed == ReadTelemetryFlush(
            ok=True,
            detail="stamped 2/2 key(s) read since the last flush",
            keys_read=2,
            keys_stamped=2,
        )
        control_sql, control_keys = conn.fetch.await_args.args
        assert "FROM app_settings" in control_sql
        assert set(control_keys) == _CONTROL_KEYS
        _sql, keys, restamp_seconds = conn.execute.await_args.args
        assert keys == ["pro_delivery_enabled", "tap_chunk_max_chars"]
        assert restamp_seconds == 600
        assert settings_read_sink.drain_read_keys() == []

    async def test_missing_control_rows_fall_back_to_the_defaults(self):
        settings_read_sink.record_read("site_url")
        pool, conn = _make_pool(execute_status="UPDATE 1")

        flushed = await flush_read_telemetry(pool)

        assert flushed.keys_stamped == 1
        assert conn.execute.await_args.args[2] == 3600

    async def test_disabled_in_the_db_discards_without_writing(self):
        settings_read_sink.record_read("site_url")
        pool, conn = _make_pool(
            control_rows=[{"key": "settings_read_telemetry_enabled", "value": "false"}]
        )

        flushed = await flush_read_telemetry(pool)

        assert flushed.ok is True
        assert "telemetry disabled" in flushed.detail
        assert flushed.keys_read == 1
        conn.execute.assert_not_awaited()
        assert settings_read_sink.drain_read_keys() == []

    async def test_a_failed_controls_query_is_reported_not_raised(self, caplog):
        settings_read_sink.record_read("site_url")
        pool, conn = _make_pool(fetch_error=OSError("connection reset by peer"))

        with caplog.at_level("WARNING"):
            flushed = await flush_read_telemetry(pool)

        assert flushed.ok is False
        assert "connection reset by peer" in flushed.detail
        conn.execute.assert_not_awaited()
        assert any("last_read_at UPDATE failed" in r.message for r in caplog.records)


@pytest.mark.unit
class TestAffectedRows:
    @pytest.mark.parametrize(
        ("status", "expected"),
        [("UPDATE 5", 5), ("UPDATE 0", 0), ("", 0), (None, 0), ("UPDATE", 0)],
    )
    def test_parses_the_command_tag_and_degrades_to_zero(self, status, expected):
        assert _affected_rows(status) == expected
