"""The morning brief dates its header by the operator's local day."""
from __future__ import annotations

from datetime import datetime, timezone
from zoneinfo import ZoneInfo

from services import clock
from services.jobs import morning_brief as mb


def test_brief_date_header_uses_operator_local_day(monkeypatch):
    # 2026-07-11 03:00 UTC == 2026-07-10 23:00 in America/New_York (EDT), so
    # the header must show the operator-local day (07-10), not the UTC day.
    #
    # Patch services.clock.datetime — the reference today_local() reads — and
    # NOT the global datetime.datetime class. freezegun swaps the global class
    # process-wide, which collides with Pydantic v2 schema generation once the
    # full suite has models loaded (PydanticSchemaGenerationError raised inside
    # freeze_time.start()). A scoped, auto-restored monkeypatch of one module's
    # attribute mirrors the tests/unit/services/test_brain_daemon_digest.py
    # idiom and sidesteps that entirely.
    real_datetime = datetime

    class _FixedDatetime(real_datetime):
        @classmethod
        def now(cls, tz=None):
            fixed = real_datetime(2026, 7, 11, 3, 0, tzinfo=timezone.utc)
            return fixed.astimezone(tz) if tz is not None else fixed.replace(tzinfo=None)

    monkeypatch.setattr(clock, "datetime", _FixedDatetime)

    data = {
        "published_count": 0, "published_rows": [],
        "awaiting_count": 0, "awaiting_rows": [],
        "alerts_by_severity": {}, "top_alertname_by_severity": {},
        "cost_cloud_usd": 0.0, "cost_cloud_calls": 0, "cost_local_calls": 0,
        "cost_electricity_usd": 0.0,
        "failed_tasks_count": 0, "failed_rows": [],
        "open_prs": [], "brain_probe_failures": 0, "brain_probe_cycles": 0,
    }
    msg = mb._format_brief(data, 24, "", 1800, ZoneInfo("America/New_York"))
    assert "2026-07-10" in msg  # operator-local day, not the 07-11 UTC day
