from __future__ import annotations

from datetime import datetime, timezone
from zoneinfo import ZoneInfo

import pytest

pytest.importorskip("apscheduler")

from apscheduler.triggers.cron import CronTrigger
from apscheduler.triggers.interval import IntervalTrigger

from plugins import scheduler as sched


def test_cron_parsed_in_operator_tz():
    tz = ZoneInfo("America/New_York")
    trig = sched._parse_schedule("0 7 * * *", tz)
    assert isinstance(trig, CronTrigger)
    # Next fire after a fixed UTC instant lands at 07:00 local == 11:00 UTC (EDT).
    after = datetime(2026, 7, 1, 6, 0, tzinfo=timezone.utc)
    nxt = trig.get_next_fire_time(None, after)
    assert nxt.astimezone(ZoneInfo("America/New_York")).hour == 7
    assert nxt.astimezone(timezone.utc).hour == 11


def test_interval_is_timezone_agnostic():
    tz = ZoneInfo("America/New_York")
    trig = sched._parse_schedule("every 30 minutes", tz)
    assert isinstance(trig, IntervalTrigger)


def test_scheduler_resolves_tz_from_site_config():
    class _Cfg:
        def get(self, key, default=""):
            return "America/New_York" if key == "operator_timezone" else default

    ps = sched.PluginScheduler(pool=object(), site_config=_Cfg())
    assert ps._tz.key == "America/New_York"


def test_scheduler_defaults_to_utc_without_site_config():
    ps = sched.PluginScheduler(pool=object())
    assert ps._tz.key == "UTC"
