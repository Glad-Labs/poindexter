"""Unit tests for ``services/integrations/handlers/tap_corsair_csv.py``.

The Corsair iCUE LINK CSV ingest tap. Tests pin the seven decision
points the handler makes:

1. **Helper parsing** — ``_parse_value`` strips Corsair unit suffixes;
   ``_parse_timestamp`` handles both d/m/yyyy and m/d/yyyy locales.
2. **Happy path** — newest file detected, rows parsed, samples
   INSERTed, cursor advanced.
3. **First run** — byte_offset=0 reads the header + body in one pass.
4. **Continuation run** — byte_offset>0 skips the partial first line.
5. **File rotation** — cursor's current_file != newest → cursor resets.
6. **Poll gate** — ``last_sample_at`` within poll_interval_minutes
   returns 0 records.
7. **Cap** — max_rows_per_run defers the remainder, byte_offset doesn't
   advance past the cap.
8. **Unmapped metrics** — no headers in row.config.metrics match → cursor
   advances to EOF anyway (operator misconfig shouldn't pin the worker
   re-reading the same bytes forever).
"""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest

from services.integrations.handlers.tap_corsair_csv import (
    _derive_offset_hours_from_mtime,
    _parse_timestamp,
    _parse_value,
    corsair_csv,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


_CSV_HEADER = (
    "Timestamp,CPU Package,CPU Load,GPU Temp #1,HX1500i Power Out,"
    " XD6 Pump: Pump\n"
)


def _csv_row(ts: str, cpu: str, load: str, gpu: str, psu_w: str, pump: str) -> str:
    return f"{ts},{cpu},{load},{gpu},{psu_w},{pump}\n"


def _write_csv(directory: Path, name: str, body: str) -> Path:
    """Write a CSV at ``directory/name`` and return the path."""
    path = directory / name
    # iCUE writes UTF-8 BOM only on the first line of the file.
    path.write_bytes(("﻿" + body).encode("utf-8"))
    return path


def _make_pool() -> tuple[MagicMock, AsyncMock]:
    """asyncpg pool stub. Returns (pool, conn) so tests can inspect calls.

    ``conn.executemany`` captures the bulk INSERT; ``conn.execute``
    captures the state-update UPDATE.
    """
    conn = AsyncMock()
    conn.executemany = AsyncMock(return_value=None)
    conn.execute = AsyncMock(return_value=None)
    ctx = AsyncMock()
    ctx.__aenter__ = AsyncMock(return_value=conn)
    ctx.__aexit__ = AsyncMock(return_value=False)
    pool = MagicMock()
    pool.acquire = MagicMock(return_value=ctx)
    return pool, conn


def _config(directory: Path, **overrides: Any) -> dict[str, Any]:
    base = {
        "directory": str(directory),
        "filename_glob": "corsair_cue_*.csv",
        "poll_interval_minutes": 0,  # tests: always-run
        "max_rows_per_run": 10000,
        "metrics": {
            "CPU Package": {"name": "cpu_package_temp", "unit": "celsius"},
            "CPU Load": {"name": "cpu_load_pct", "unit": "percent"},
            "GPU Temp #1": {"name": "gpu_temp", "unit": "celsius"},
            "HX1500i Power Out": {"name": "psu_power_out", "unit": "watts"},
            " XD6 Pump: Pump": {"name": "pump_rpm", "unit": "rpm"},
        },
    }
    base.update(overrides)
    return base


# ---------------------------------------------------------------------------
# Helper-function tests
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestParseValue:
    def test_celsius(self):
        assert _parse_value("53.75°C") == 53.75

    def test_volts(self):
        assert _parse_value("1.34V") == 1.34

    def test_amps(self):
        assert _parse_value("19.50A") == 19.5

    def test_watts(self):
        assert _parse_value("234W") == 234.0

    def test_rpm(self):
        assert _parse_value("0RPM") == 0.0

    def test_percent(self):
        assert _parse_value("10%") == 10.0

    def test_bare_number(self):
        assert _parse_value("42") == 42.0

    def test_negative(self):
        assert _parse_value("-5.5°C") == -5.5

    def test_garbage_returns_none(self):
        assert _parse_value("not a number") is None

    def test_dash_placeholder_returns_none(self):
        # Corsair sometimes prints a unicode em-dash for "no data".
        assert _parse_value("—") is None

    def test_empty_returns_none(self):
        assert _parse_value("") is None
        assert _parse_value(None) is None  # type: ignore[arg-type]


@pytest.mark.unit
class TestParseTimestamp:
    def test_dmy_format_zero_offset(self):
        # local_offset_hours=0 → treat the string as UTC directly.
        result = _parse_timestamp("12/5/2026 08:56:48 AM")
        assert result is not None
        assert result.year == 2026
        # d/m/y wins because it's first in the format list — Matt's
        # locale per the CSV he sent.
        assert result.month == 5
        assert result.day == 12
        assert result.hour == 8

    def test_iso_zero_offset(self):
        result = _parse_timestamp("2026-05-12T08:56:48")
        assert result == datetime(2026, 5, 12, 8, 56, 48, tzinfo=timezone.utc)

    def test_edt_offset_subtracts_four_hours(self):
        # Matt's local clock 09:30 EDT (= UTC-4) → 13:30 UTC.
        # This is the bug-fix path: without the offset, "09:30 AM"
        # in the CSV landed in the DB as 09:30 UTC, so Grafana's
        # "now() - 1h" window missed every row by 4 hours.
        result = _parse_timestamp(
            "12/5/2026 09:30:00 AM", local_offset_hours=-4.0,
        )
        assert result == datetime(2026, 5, 12, 13, 30, 0, tzinfo=timezone.utc)

    def test_est_offset_subtracts_five_hours(self):
        # Same wall-clock in winter (EST UTC-5) → 14:30 UTC.
        result = _parse_timestamp(
            "12/5/2026 09:30:00 AM", local_offset_hours=-5.0,
        )
        assert result == datetime(2026, 5, 12, 14, 30, 0, tzinfo=timezone.utc)

    def test_invalid_returns_none(self):
        assert _parse_timestamp("not a timestamp") is None
        assert _parse_timestamp("") is None


# ---------------------------------------------------------------------------
# TZ derivation tests (poindexter#484)
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestDeriveOffsetHoursFromMtime:
    """Verify mtime-vs-last-row derivation produces the right operator offset."""

    def _write_csv(self, path: Path, last_row_local: datetime) -> None:
        # iCUE format is `d/m/yyyy HH:MM:SS AM/PM` (Windows locale).
        # AM/PM is redundant with 24h clock but the parser expects it.
        body = (
            "Timestamp,CPU Package\n"
            f"01/01/2026 00:00:00 AM,40\n"
            f"{last_row_local.strftime('%d/%m/%Y %H:%M:%S')} {'AM' if last_row_local.hour < 12 else 'PM'},50\n"
        )
        path.write_bytes(("﻿" + body).encode("utf-8"))

    def test_edt_offset_minus_four(self, tmp_path: Path):
        # iCUE wrote 12:00 local; file was saved at 16:00 UTC → EDT (-4).
        path = tmp_path / "cue.csv"
        self._write_csv(path, datetime(2026, 5, 12, 12, 0, 0))
        import os
        target_mtime_utc = datetime(2026, 5, 12, 16, 0, 0, tzinfo=timezone.utc)
        os.utime(path, (target_mtime_utc.timestamp(), target_mtime_utc.timestamp()))
        assert _derive_offset_hours_from_mtime(path) == -4.0

    def test_est_offset_minus_five(self, tmp_path: Path):
        path = tmp_path / "cue.csv"
        self._write_csv(path, datetime(2026, 1, 15, 12, 0, 0))
        import os
        target_mtime_utc = datetime(2026, 1, 15, 17, 0, 0, tzinfo=timezone.utc)
        os.utime(path, (target_mtime_utc.timestamp(), target_mtime_utc.timestamp()))
        assert _derive_offset_hours_from_mtime(path) == -5.0

    def test_jst_offset_plus_nine(self, tmp_path: Path):
        # iCUE wrote 21:00 local; file was saved at 12:00 UTC → JST (+9).
        path = tmp_path / "cue.csv"
        self._write_csv(path, datetime(2026, 5, 12, 21, 0, 0))
        import os
        target_mtime_utc = datetime(2026, 5, 12, 12, 0, 0, tzinfo=timezone.utc)
        os.utime(path, (target_mtime_utc.timestamp(), target_mtime_utc.timestamp()))
        assert _derive_offset_hours_from_mtime(path) == 9.0

    def test_unparseable_last_row_returns_none(self, tmp_path: Path):
        path = tmp_path / "cue.csv"
        path.write_bytes(("﻿Timestamp,Metric\nnot-a-timestamp,123\n").encode("utf-8"))
        assert _derive_offset_hours_from_mtime(path) is None

    def test_empty_file_returns_none(self, tmp_path: Path):
        path = tmp_path / "cue.csv"
        path.write_bytes(b"")
        assert _derive_offset_hours_from_mtime(path) is None

    def test_drift_beyond_real_timezones_returns_none(self, tmp_path: Path):
        # A 20-hour delta is past any real timezone (max +14 / min -12).
        # Likely manual backdating or clock skew; don't apply a nonsense
        # offset.
        path = tmp_path / "cue.csv"
        self._write_csv(path, datetime(2026, 5, 12, 0, 0, 0))
        import os
        target_mtime_utc = datetime(2026, 5, 12, 20, 0, 0, tzinfo=timezone.utc)
        os.utime(path, (target_mtime_utc.timestamp(), target_mtime_utc.timestamp()))
        assert _derive_offset_hours_from_mtime(path) is None


# ---------------------------------------------------------------------------
# Handler tests
# ---------------------------------------------------------------------------


@pytest.mark.unit
@pytest.mark.asyncio
class TestCorsairCsvHandler:

    async def test_directory_missing_raises(self, tmp_path: Path):
        pool, _ = _make_pool()
        row = {"id": "r1", "config": {"directory": ""}, "state": {}}
        with pytest.raises(ValueError, match="directory is required"):
            await corsair_csv(None, site_config=None, row=row, pool=pool)

    async def test_no_matching_files_returns_zero(self, tmp_path: Path):
        pool, conn = _make_pool()
        row = {"id": "r1", "config": _config(tmp_path), "state": {}}
        result = await corsair_csv(None, site_config=None, row=row, pool=pool)
        assert result["records"] == 0
        assert "no files found" in result["reason"]
        conn.executemany.assert_not_called()

    async def test_happy_path_first_run(self, tmp_path: Path):
        """First run (byte_offset=0): header + 2 data rows → 10 samples
        (5 metrics × 2 rows). Cursor advances to EOF.
        """
        body = (
            _CSV_HEADER
            + _csv_row("12/5/2026 09:00:00 AM", "60.5°C", "20%", "50.0°C", "200W", "3000RPM")
            + _csv_row("12/5/2026 09:00:30 AM", "62.0°C", "25%", "51.0°C", "220W", "3050RPM")
        )
        _write_csv(tmp_path, "corsair_cue_20260512_090000.csv", body)
        pool, conn = _make_pool()
        row = {"id": "r1", "config": _config(tmp_path), "state": {}}

        result = await corsair_csv(None, site_config=None, row=row, pool=pool)

        assert result["records"] == 10  # 5 metrics × 2 rows
        assert result["rows_parsed"] == 2
        assert result["file"] == "corsair_cue_20260512_090000.csv"

        # executemany called once with all 10 sample tuples.
        conn.executemany.assert_awaited_once()
        sql, samples = conn.executemany.await_args.args
        assert "INSERT INTO sensor_samples" in sql
        assert "ON CONFLICT" in sql
        assert len(samples) == 10
        # Each tuple: (source, metric_name, value, unit, sampled_at)
        sources = {s[0] for s in samples}
        assert sources == {"corsair_csv"}
        metric_names = {s[1] for s in samples}
        assert metric_names == {
            "cpu_package_temp", "cpu_load_pct", "gpu_temp",
            "psu_power_out", "pump_rpm",
        }
        # First sample is CPU temp at first timestamp.
        cpu_temps = [s for s in samples if s[1] == "cpu_package_temp"]
        cpu_temps.sort(key=lambda s: s[4])
        assert cpu_temps[0][2] == 60.5
        assert cpu_temps[1][2] == 62.0

        # State persisted: cursor advanced to EOF, current_file recorded.
        state_update = next(
            call for call in conn.execute.await_args_list
            if "UPDATE external_taps" in call.args[0]
        )
        new_state = json.loads(state_update.args[1])
        assert new_state["current_file"] == "corsair_cue_20260512_090000.csv"
        assert new_state["byte_offset"] > 0
        assert new_state["rows_processed_total"] == 2
        assert new_state["last_sample_at"].startswith("2026-05-12T09:00:30")

    async def test_continuation_run_skips_partial_first_row(self, tmp_path: Path):
        """byte_offset > 0: cursor sits mid-row. Handler must advance to
        the next newline before parsing so we don't emit a malformed row.
        """
        full_body = (
            _CSV_HEADER
            + _csv_row("12/5/2026 09:00:00 AM", "60.5°C", "20%", "50.0°C", "200W", "3000RPM")
            + _csv_row("12/5/2026 09:00:30 AM", "62.0°C", "25%", "51.0°C", "220W", "3050RPM")
            + _csv_row("12/5/2026 09:01:00 AM", "63.0°C", "30%", "52.0°C", "240W", "3100RPM")
        )
        path = _write_csv(tmp_path, "corsair_cue_20260512_090000.csv", full_body)
        # Set cursor halfway into the second data row to simulate the
        # mid-row resume case.
        bom_offset = 1  # The UTF-8 BOM occupies 1 char but 3 bytes; offset by 3.
        # Calculate: first \n is end of header; row1 starts after that.
        encoded = path.read_bytes()
        row1_start = encoded.find(b"\n") + 1
        row2_start = encoded.find(b"\n", row1_start) + 1
        mid_row2 = row2_start + 5  # 5 bytes into row2
        del bom_offset  # silence the linter; just used for the comment

        pool, conn = _make_pool()
        row = {
            "id": "r1",
            "config": _config(tmp_path),
            "state": {
                "current_file": "corsair_cue_20260512_090000.csv",
                "byte_offset": mid_row2,
            },
        }
        result = await corsair_csv(None, site_config=None, row=row, pool=pool)

        # Row 2 dropped (partial), Row 3 parsed cleanly.
        assert result["rows_parsed"] == 1
        assert result["records"] == 5  # 5 metrics × 1 row
        samples = conn.executemany.await_args.args[1]
        # The only timestamp captured is the third row's.
        sampled_ats = {s[4].isoformat() for s in samples}
        assert all("2026-05-12T09:01:00" in ts for ts in sampled_ats)

    async def test_file_rotation_resets_cursor(self, tmp_path: Path):
        """A new CSV in the directory (newer mtime) → cursor resets to 0
        on the new file's first poll.
        """
        # Old file with a stale cursor pointing at it.
        old_body = (
            _CSV_HEADER
            + _csv_row("11/5/2026 19:05:19 PM", "50°C", "10%", "40°C", "100W", "2900RPM")
        )
        _write_csv(tmp_path, "corsair_cue_20260511_19_05_19.csv", old_body)
        # New file appears (newer mtime — pathlib stat). Touch by writing
        # last so its mtime wins.
        new_body = (
            _CSV_HEADER
            + _csv_row("12/5/2026 09:00:00 AM", "55°C", "15%", "45°C", "150W", "3000RPM")
        )
        _write_csv(tmp_path, "corsair_cue_20260512_09_00_00.csv", new_body)

        pool, conn = _make_pool()
        row = {
            "id": "r1",
            "config": _config(tmp_path),
            "state": {
                "current_file": "corsair_cue_20260511_19_05_19.csv",
                "byte_offset": 9999,  # past EOF on the old file
                "rows_processed_total": 1,
            },
        }
        result = await corsair_csv(None, site_config=None, row=row, pool=pool)

        # New file picked up, cursor reset, row parsed cleanly.
        assert result["file"] == "corsair_cue_20260512_09_00_00.csv"
        assert result["rows_parsed"] == 1
        # Cursor advanced from 0 (reset) to EOF of the new file.
        state_update = next(
            call for call in conn.execute.await_args_list
            if "UPDATE external_taps" in call.args[0]
        )
        new_state = json.loads(state_update.args[1])
        assert new_state["current_file"] == "corsair_cue_20260512_09_00_00.csv"

    async def test_poll_interval_gate(self, tmp_path: Path):
        """last_sample_at within poll_interval_minutes → return 0
        without touching the filesystem.
        """
        # Create a file so directory exists, but the gate should prevent
        # us from ever reading it.
        body = _CSV_HEADER + _csv_row(
            "12/5/2026 09:00:00 AM", "60°C", "20%", "50°C", "200W", "3000RPM",
        )
        _write_csv(tmp_path, "corsair_cue_20260512_090000.csv", body)
        pool, conn = _make_pool()
        # last_sample_at 2 min ago, poll_interval=5 → not due.
        recent = (datetime.now(timezone.utc) - timedelta(minutes=2)).isoformat()
        row = {
            "id": "r1",
            "config": _config(tmp_path, poll_interval_minutes=5),
            "state": {"last_sample_at": recent},
        }
        result = await corsair_csv(None, site_config=None, row=row, pool=pool)
        assert result["records"] == 0
        assert result["reason"] == "not due"
        conn.executemany.assert_not_called()
        conn.execute.assert_not_called()

    async def test_max_rows_per_run_cap(self, tmp_path: Path):
        """Backlog of 10 rows but cap=3 → only 3 parsed, cursor stays put
        so the remaining 7 land next cycle.
        """
        rows = "".join(
            _csv_row(
                f"12/5/2026 09:00:{i:02d} AM",
                f"{60+i}°C", "20%", "50°C", "200W", "3000RPM",
            )
            for i in range(10)
        )
        body = _CSV_HEADER + rows
        _write_csv(tmp_path, "corsair_cue_20260512_090000.csv", body)
        pool, conn = _make_pool()
        row = {
            "id": "r1",
            "config": _config(tmp_path, max_rows_per_run=3),
            "state": {},
        }
        result = await corsair_csv(None, site_config=None, row=row, pool=pool)
        # Parser sees 4 rows (3 valid + 1 over-cap that triggers break).
        # Only the first 3 commit; the 4th was the trigger.
        assert result["records"] == 15  # 5 metrics × 3 rows
        # Cursor does NOT advance to EOF (so the remaining rows are
        # available next cycle).
        state_update = next(
            call for call in conn.execute.await_args_list
            if "UPDATE external_taps" in call.args[0]
        )
        new_state = json.loads(state_update.args[1])
        assert new_state["byte_offset"] == 0  # unchanged from initial

    async def test_no_mapped_metrics_advances_cursor(self, tmp_path: Path):
        """Operator misconfig: row.config.metrics is empty / doesn't
        match any header. Handler logs + advances cursor to EOF anyway
        so it doesn't re-read the same bytes forever.
        """
        body = _CSV_HEADER + _csv_row(
            "12/5/2026 09:00:00 AM", "60°C", "20%", "50°C", "200W", "3000RPM",
        )
        _write_csv(tmp_path, "corsair_cue_20260512_090000.csv", body)
        pool, conn = _make_pool()
        row = {
            "id": "r1",
            "config": _config(tmp_path, metrics={"NotARealColumn": {"name": "x", "unit": ""}}),
            "state": {},
        }
        result = await corsair_csv(None, site_config=None, row=row, pool=pool)
        assert result["records"] == 0
        assert "no metrics matched" in result["reason"]
        # State cursor still got updated so we don't loop forever on
        # the same bytes.
        state_update = next(
            call for call in conn.execute.await_args_list
            if "UPDATE external_taps" in call.args[0]
        )
        new_state = json.loads(state_update.args[1])
        assert new_state["byte_offset"] > 0

    async def test_unparseable_cells_skipped_silently(self, tmp_path: Path):
        """Corsair sometimes emits "—" for unavailable cells. Those skip
        without dropping the whole row.
        """
        body = (
            _CSV_HEADER
            + _csv_row("12/5/2026 09:00:00 AM", "60°C", "—", "50°C", "—", "3000RPM")
        )
        _write_csv(tmp_path, "corsair_cue_20260512_090000.csv", body)
        pool, conn = _make_pool()
        row = {"id": "r1", "config": _config(tmp_path), "state": {}}
        result = await corsair_csv(None, site_config=None, row=row, pool=pool)
        # 3 valid (cpu_temp, gpu_temp, pump) out of 5 cells. The 2
        # em-dashed cells skip silently.
        assert result["records"] == 3
        assert result["rows_parsed"] == 1
        samples = conn.executemany.await_args.args[1]
        names = {s[1] for s in samples}
        assert names == {"cpu_package_temp", "gpu_temp", "pump_rpm"}

    async def test_no_new_bytes_returns_zero(self, tmp_path: Path):
        """Cursor at EOF, file hasn't grown → tap is a clean noop."""
        body = _CSV_HEADER + _csv_row(
            "12/5/2026 09:00:00 AM", "60°C", "20%", "50°C", "200W", "3000RPM",
        )
        path = _write_csv(tmp_path, "corsair_cue_20260512_090000.csv", body)
        size = path.stat().st_size
        pool, conn = _make_pool()
        row = {
            "id": "r1",
            "config": _config(tmp_path),
            "state": {
                "current_file": "corsair_cue_20260512_090000.csv",
                "byte_offset": size,
            },
        }
        result = await corsair_csv(None, site_config=None, row=row, pool=pool)
        assert result["records"] == 0
        assert result["reason"] == "no new bytes"
        conn.executemany.assert_not_called()
