"""SyncPodcastDownloadsJob: R2 episode fetches → external_metrics (2026-10-27)."""

from __future__ import annotations

from datetime import UTC, date, datetime, timedelta
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from poindexter.services.jobs.sync_podcast_downloads import (
    SOURCE,
    SyncPodcastDownloadsJob,
    tally,
)

EP = "f1476b65-cab0-4122-8a16-a3f9c3c0bf6b"
DELIVERED = datetime(2026, 10, 25, 15, 0, tzinfo=UTC)


def _group(obj: str, hour: str, requests: int, size: int = 1000) -> dict[str, Any]:
    return {"sum": {"requests": requests, "responseObjectSize": size},
            "dimensions": {"objectName": obj, "datetimeHour": hour}}


def test_fetches_inside_the_window_are_ingest_and_later_ones_are_listens():
    groups = [
        _group(f"podcast/v2/{EP}.mp3", "2026-10-25T15:00:00Z", 5),   # ingest burst
        _group(f"podcast/v2/{EP}.mp3", "2026-10-25T20:00:00Z", 1),   # still inside 6h
        _group(f"podcast/v2/{EP}.mp3", "2026-10-25T22:00:00Z", 2),   # listen
        _group(f"podcast/v2/{EP}.mp3", "2026-10-26T09:00:00Z", 1),   # listen, next day
    ]
    out = tally(groups, {EP: DELIVERED}, timedelta(hours=6))
    day1, day2 = out[(EP, date(2026, 10, 25))], out[(EP, date(2026, 10, 26))]
    assert (day1["ingest_requests"], day1["listen_requests"], day1["requests"]) == (6, 2, 8)
    assert (day2["ingest_requests"], day2["listen_requests"]) == (0, 1)


def test_an_episode_with_no_delivery_stamp_is_never_counted_as_listened():
    out = tally([_group(f"podcast/v2/{EP}.mp3", "2026-10-27T09:00:00Z", 3)], {}, timedelta(hours=6))
    row = out[(EP, date(2026, 10, 27))]
    assert row["ingest_requests"] == 3 and row["listen_requests"] == 0


def test_an_hour_that_fetches_several_episodes_is_a_crawl():
    """The 2026-09-28 shape: one hour fetched 19 episodes. Later than the
    ingest window, so without this it read as 53 listens."""
    others = [f"0f0e0d0c-0000-4000-8000-00000000000{i}" for i in range(3)]
    groups = [_group(f"podcast/v2/{EP}.mp3", "2026-10-26T18:00:00Z", 2)] + [
        _group(f"podcast/v2/{o}.mp3", "2026-10-26T18:00:00Z", 1) for o in others
    ]
    out = tally(groups, {EP: DELIVERED}, timedelta(hours=6), sweep_min_episodes=3)
    row = out[(EP, date(2026, 10, 26))]
    assert (row["crawl_requests"], row["listen_requests"]) == (2, 0)


def test_two_episodes_in_an_hour_is_below_the_sweep_threshold():
    other = "0f0e0d0c-0000-4000-8000-000000000009"
    groups = [_group(f"podcast/v2/{EP}.mp3", "2026-10-26T18:00:00Z", 1),
              _group(f"podcast/v2/{other}.mp3", "2026-10-26T18:00:00Z", 1)]
    out = tally(groups, {EP: DELIVERED}, timedelta(hours=6), sweep_min_episodes=3)
    assert out[(EP, date(2026, 10, 26))]["listen_requests"] == 1


def test_keys_that_are_not_episode_mp3s_are_ignored():
    groups = [_group("podcast/feed.xml", "2026-10-27T09:00:00Z", 900),
              _group("podcast/v2/not-a-uuid.mp3", "2026-10-27T09:00:00Z", 4)]
    assert tally(groups, {}, timedelta(hours=6)) == {}


class _Conn:
    def __init__(self, store):
        self.store = store

    async def fetchval(self, sql, *args):
        return bool(self.store["rows"])

    async def fetch(self, sql, *args):
        if "media_approvals" in sql:
            return [{"post_id": EP, "dispatched_at": DELIVERED}]
        return [{"post_id": EP, "slug": "our-dual-gpu-post"}]

    async def execute(self, sql, *args):
        self.store["rows"].append(args)


class _Pool:
    def __init__(self):
        self.store = {"rows": []}

    def acquire(self):
        conn = _Conn(self.store)

        class _Ctx:
            async def __aenter__(self_inner):
                return conn

            async def __aexit__(self_inner, *a):
                return False

        return _Ctx()


class _SC:
    def __init__(self, **values):
        self.values = {"cloudflare_account_id": "acct", "storage_bucket": "gladlabs-media", **values}

    def get(self, key, default=None):
        return self.values.get(key, default)

    async def get_secret(self, key, default=""):
        return "token"


def _cf_response(groups):
    resp = MagicMock()
    resp.raise_for_status = MagicMock()
    resp.json = MagicMock(return_value={"data": {"viewer": {"accounts": [
        {"r2OperationsAdaptiveGroups": groups}]}}})
    client = MagicMock()
    client.post = AsyncMock(return_value=resp)
    client.__aenter__ = AsyncMock(return_value=client)
    client.__aexit__ = AsyncMock(return_value=False)
    return client


@pytest.mark.asyncio
async def test_a_run_upserts_four_metrics_per_episode_day_keyed_by_slug():
    pool = _Pool()
    client = _cf_response([
        _group(f"podcast/v2/{EP}.mp3", "2026-10-25T15:00:00Z", 2, 7_000_000),
        _group(f"podcast/v2/{EP}.mp3", "2026-10-26T15:00:00Z", 1, 3_500_000),
    ])
    with patch("poindexter.services.jobs.sync_podcast_downloads.httpx.AsyncClient", return_value=client):
        result = await SyncPodcastDownloadsJob().run(pool, {"_site_config": _SC()})

    assert result.ok and result.metrics == {"episodes": 1, "requests": 3, "listen_requests": 1}
    rows = pool.store["rows"]
    assert len(rows) == 10  # 2 days x 5 metrics
    assert {r[0] for r in rows} == {SOURCE}
    assert {r[4] for r in rows} == {"our-dual-gpu-post"}
    listened = {(r[5], r[1]): r[2] for r in rows}
    assert listened[(date(2026, 10, 26), "listen_requests")] == 1
    query = client.post.await_args.kwargs["json"]
    assert query["variables"]["bucket"] == "gladlabs-media"
    since = datetime.fromisoformat(query["variables"]["since"].replace("Z", "+00:00"))
    until = datetime.fromisoformat(query["variables"]["until"].replace("Z", "+00:00"))
    assert (until - since).days == 30, "the first run backfills the initial window"


@pytest.mark.asyncio
async def test_cloudflare_errors_fail_the_run():
    pool = _Pool()
    client = _cf_response([])
    client.post.return_value.json.return_value = {"errors": [{"message": "not authorized"}]}
    with patch("poindexter.services.jobs.sync_podcast_downloads.httpx.AsyncClient", return_value=client):
        result = await SyncPodcastDownloadsJob().run(pool, {"_site_config": _SC()})
    assert result.ok is False and "not authorized" in result.detail
    assert pool.store["rows"] == []


@pytest.mark.asyncio
async def test_switched_off_does_nothing():
    result = await SyncPodcastDownloadsJob().run(
        _Pool(), {"_site_config": _SC(podcast_downloads_sync_enabled="false")},
    )
    assert result.ok and result.changes_made == 0 and "off" in result.detail
