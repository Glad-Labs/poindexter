"""EnqueueNewsShortsJob: queues at most one news Short, within the day's quota."""

from __future__ import annotations

from datetime import datetime
from unittest.mock import AsyncMock, MagicMock, patch
from zoneinfo import ZoneInfo

import pytest

from poindexter.services import news_shorts as ns
from poindexter.services.jobs.enqueue_news_shorts import EnqueueNewsShortsJob


class _SC:
    def __init__(self, **values):
        self.values = {"news_shorts_enabled": "true", **values}

    def get(self, key, default=None):
        return self.values.get(key, default)

    def get_int(self, key, default=0):
        return int(self.values.get(key, default))


def _pool(in_flight=0):
    pool = MagicMock()
    pool.fetchval = AsyncMock(return_value=in_flight)
    return pool


async def _run(sc, *, hour=10, made=0, in_flight=0, picked=None):
    tz = ZoneInfo("UTC")
    with patch("poindexter.services.clock.get_operator_tz", AsyncMock(return_value=tz)), \
         patch("poindexter.services.clock.now_local", return_value=datetime(2026, 10, 18, hour, tzinfo=tz)), \
         patch.object(ns, "made_since", AsyncMock(return_value=made)) as made_since, \
         patch.object(ns, "pick_story", AsyncMock(return_value=picked or ns.PickResult(note="none"))) as pick, \
         patch.object(ns, "enqueue_news_short", AsyncMock(return_value="t-9")) as enqueue:
        result = await EnqueueNewsShortsJob().run(_pool(in_flight), {"_site_config": sc})
    return result, made_since, pick, enqueue


@pytest.mark.asyncio
async def test_off_by_default():
    result = await EnqueueNewsShortsJob().run(_pool(), {"_site_config": _SC(news_shorts_enabled="false")})
    assert result.ok and result.changes_made == 0
    assert "disabled" in result.detail


@pytest.mark.asyncio
async def test_nothing_is_queued_before_the_earliest_hour():
    result, _m, pick, _e = await _run(_SC(news_shorts_earliest_hour="8"), hour=6)
    assert result.changes_made == 0 and "earliest_hour" in result.detail
    pick.assert_not_called()


@pytest.mark.asyncio
async def test_the_days_quota_counts_from_local_midnight():
    result, made_since, pick, _e = await _run(_SC(news_shorts_per_day="1"), made=1)
    assert result.changes_made == 0 and "1/1" in result.detail
    assert made_since.call_args.args[1] == datetime(2026, 10, 18, tzinfo=ZoneInfo("UTC"))
    pick.assert_not_called()


@pytest.mark.asyncio
async def test_a_task_in_flight_holds_the_next_one():
    result, _m, pick, _e = await _run(_SC(), in_flight=1)
    assert result.changes_made == 0 and "in flight" in result.detail
    pick.assert_not_called()


@pytest.mark.asyncio
async def test_no_story_queues_nothing():
    result, _m, _p, enqueue = await _run(_SC(), picked=ns.PickResult(considered=4, note="none in scope"))
    assert result.ok and result.changes_made == 0
    assert result.detail == "none in scope"
    enqueue.assert_not_called()


@pytest.mark.asyncio
async def test_a_picked_story_is_queued_for_the_configured_niche():
    story = ns.NewsStory(pool_id="p", source="rss", title="Cards vanish", url="https://e.com")
    result, _m, _p, enqueue = await _run(
        _SC(news_shorts_niche="starter-blog"), picked=ns.PickResult(story=story, considered=3, in_scope=1),
    )
    assert result.changes_made == 1
    assert result.metrics["task_id"] == "t-9"
    assert enqueue.call_args.args[1] is story
    assert enqueue.call_args.kwargs == {"niche": "starter-blog"}


@pytest.mark.asyncio
async def test_a_failed_pick_is_reported_not_raised():
    tz = ZoneInfo("UTC")
    with patch("poindexter.services.clock.get_operator_tz", AsyncMock(return_value=tz)), \
         patch("poindexter.services.clock.now_local", return_value=datetime(2026, 10, 18, 10, tzinfo=tz)), \
         patch.object(ns, "made_since", AsyncMock(return_value=0)), \
         patch.object(ns, "pick_story", AsyncMock(side_effect=RuntimeError("db down"))):
        result = await EnqueueNewsShortsJob().run(_pool(), {"_site_config": _SC()})
    assert not result.ok and "db down" in result.detail
