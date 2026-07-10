"""Contract tests for the system clock (operator timezone)."""
from __future__ import annotations

from datetime import datetime, timezone

import pytest

from services import clock


def test_resolve_valid_name():
    tz = clock.resolve_operator_tz("America/New_York")
    assert tz.key == "America/New_York"


@pytest.mark.parametrize("value", [None, "", "   "])
def test_resolve_missing_defaults_to_utc(value):
    assert clock.resolve_operator_tz(value).key == "UTC"


def test_resolve_invalid_is_loud_and_degrades(monkeypatch):
    calls = []
    monkeypatch.setattr(clock, "emit_finding", lambda **kw: calls.append(kw), raising=False)
    tz = clock.resolve_operator_tz("Amrica/New_York")  # typo
    assert tz.key == "UTC"                              # degraded, did not raise
    assert calls and calls[0]["severity"] == "warn"    # loud
    assert "Amrica/New_York" in calls[0]["body"]


def test_dst_summer_maps_7am_local_to_11utc():
    # 2026-07-01 is EDT (UTC-4): 07:00 local == 11:00 UTC.
    tz = clock.resolve_operator_tz("America/New_York")
    local_7am = datetime(2026, 7, 1, 7, 0, tzinfo=tz)
    assert local_7am.astimezone(timezone.utc).hour == 11


def test_dst_winter_maps_7am_local_to_12utc():
    # 2026-01-01 is EST (UTC-5): 07:00 local == 12:00 UTC.
    tz = clock.resolve_operator_tz("America/New_York")
    local_7am = datetime(2026, 1, 1, 7, 0, tzinfo=tz)
    assert local_7am.astimezone(timezone.utc).hour == 12


def test_today_local_crosses_utc_midnight(monkeypatch):
    # 2026-07-11 03:00 UTC == 2026-07-10 23:00 in ET; today_local must report
    # the operator-local day. Patch services.clock.datetime (what today_local
    # reads), NOT the global datetime.datetime class — freezegun's global swap
    # collides with Pydantic schema generation in the full suite.
    real_datetime = datetime

    class _FixedDatetime(real_datetime):
        @classmethod
        def now(cls, tz=None):
            fixed = real_datetime(2026, 7, 11, 3, 0, tzinfo=timezone.utc)
            return fixed.astimezone(tz) if tz is not None else fixed.replace(tzinfo=None)

    monkeypatch.setattr(clock, "datetime", _FixedDatetime)
    tz = clock.resolve_operator_tz("America/New_York")
    assert clock.today_local(tz).isoformat() == "2026-07-10"


def test_to_local_treats_naive_as_utc():
    tz = clock.resolve_operator_tz("America/New_York")
    naive = datetime(2026, 7, 1, 11, 0)  # no tzinfo -> assume UTC
    assert clock.to_local(naive, tz).hour == 7


def test_format_local():
    tz = clock.resolve_operator_tz("America/New_York")
    dt = datetime(2026, 7, 1, 11, 0, tzinfo=timezone.utc)
    assert clock.format_local(dt, tz, "%Y-%m-%d %H:%M") == "2026-07-01 07:00"
