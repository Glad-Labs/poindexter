"""youtube_upload_slots: an approved video goes public at the next free slot.

Pins slot choice (operator-local times, the lead, one upload per slot per
type, other type ignored), the fall-backs to publishing immediately, and the
publishAt format YouTube needs.
"""

from __future__ import annotations

import datetime as dt
import json
from unittest.mock import AsyncMock, MagicMock

import pytest

from poindexter.services import youtube_upload_slots as us
from poindexter.services.site_config import SiteConfig

UTC = dt.timezone.utc


def _sc(**kw):
    base = {"youtube_upload_slots": "short=12:00,19:00; long=17:00", "operator_timezone": "America/New_York"}
    base.update(kw)
    return SiteConfig(initial_config=base)


def _pool(stamps=()):
    conn = MagicMock()
    conn.fetch = AsyncMock(return_value=[{"publish_at": s} for s in stamps])
    conn.execute = AsyncMock()
    pool = MagicMock()

    class _Acq:
        async def __aenter__(self):
            return conn

        async def __aexit__(self, *a):
            return False

    pool.acquire = lambda: _Acq()
    return pool, conn


# 2026-10-18 10:00 New York (EDT, UTC-4) == 14:00 UTC
NOW = dt.datetime(2026, 10, 18, 14, 0, tzinfo=UTC)


class TestParse:
    def test_parses_and_sorts(self):
        assert us.parse_slots("long=17:00; short=19:00,12:00,12:00") == {
            "long": [dt.time(17, 0)], "short": [dt.time(12, 0), dt.time(19, 0)],
        }

    @pytest.mark.parametrize("spec", ["", None, "podcast=09:00", "short", "short=25:00"])
    def test_bad_entries_are_dropped(self, spec):
        assert us.parse_slots(spec) == {}

    def test_the_seeded_setting_parses(self):
        from poindexter.services.settings_defaults import DEFAULTS

        slots = us.parse_slots(DEFAULTS["youtube_upload_slots"])
        assert set(slots) == {"short", "long"}
        assert DEFAULTS["youtube_upload_slots_enabled"] == "true"


class TestNextSlot:
    async def test_the_next_local_slot_after_the_lead(self):
        pool, conn = _pool()
        when = await us.next_upload_slot(pool, _sc(), shorts=True, now=NOW)
        assert when == dt.datetime(2026, 10, 18, 16, 0, tzinfo=UTC)  # 12:00 New York
        assert conn.fetch.await_args.args[1] == "video_short"

    async def test_the_lead_pushes_past_a_slot_too_close(self):
        pool, _ = _pool()
        at_1145 = dt.datetime(2026, 10, 18, 15, 45, tzinfo=UTC)  # 11:45 NY, 15 min before noon
        when = await us.next_upload_slot(pool, _sc(), shorts=True, now=at_1145)
        assert when == dt.datetime(2026, 10, 18, 23, 0, tzinfo=UTC)  # 19:00 NY

    async def test_a_taken_slot_is_skipped(self):
        pool, _ = _pool(["2026-10-18T16:00:00Z", "2026-10-18T23:00:00Z"])
        when = await us.next_upload_slot(pool, _sc(), shorts=True, now=NOW)
        assert when == dt.datetime(2026, 10, 19, 16, 0, tzinfo=UTC)  # tomorrow noon

    async def test_long_videos_use_their_own_times(self):
        pool, conn = _pool()
        when = await us.next_upload_slot(pool, _sc(), shorts=False, now=NOW)
        assert when == dt.datetime(2026, 10, 18, 21, 0, tzinfo=UTC)  # 17:00 NY
        assert conn.fetch.await_args.args[1] == "video"

    async def test_past_and_malformed_stamps_do_not_block(self):
        pool, _ = _pool(["2026-10-17T16:00:00Z", "garbage", None])
        when = await us.next_upload_slot(pool, _sc(), shorts=True, now=NOW)
        assert when == dt.datetime(2026, 10, 18, 16, 0, tzinfo=UTC)

    @pytest.mark.parametrize("cfg", [
        {"youtube_upload_slots_enabled": "false"},
        {"youtube_upload_slots": "long=17:00"},  # no Short times
        {"youtube_upload_slots": ""},
    ])
    async def test_no_slot_means_publish_now(self, cfg):
        pool, conn = _pool()
        assert await us.next_upload_slot(pool, _sc(**cfg), shorts=True, now=NOW) is None

    async def test_a_query_failure_still_picks_a_slot(self):
        pool = MagicMock()
        pool.acquire = MagicMock(side_effect=RuntimeError("db down"))
        when = await us.next_upload_slot(pool, _sc(), shorts=True, now=NOW)
        assert when == dt.datetime(2026, 10, 18, 16, 0, tzinfo=UTC)


def test_rfc3339():
    assert us.rfc3339(dt.datetime(2026, 10, 18, 16, 0, 0, 123456, tzinfo=UTC)) == "2026-10-18T16:00:00Z"


async def test_record_slot_stamps_the_row():
    pool, conn = _pool()
    await us.record_slot(pool, "00000000-0000-0000-0000-000000000001", dt.datetime(2026, 10, 18, 16, tzinfo=UTC))
    _sql, _aid, key, payload = conn.execute.await_args.args
    assert key == "youtube_publish_at" and json.loads(payload) == "2026-10-18T16:00:00Z"
