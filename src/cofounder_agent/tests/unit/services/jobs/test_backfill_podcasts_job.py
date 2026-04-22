"""Unit tests for ``services/jobs/backfill_podcasts.py``.

Covers the two-pass (R2 sync + generation) workflow plus the
RSS-feed-rebuild side effect. Podcast + R2 services are mocked.
"""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from services.jobs.backfill_podcasts import BackfillPodcastsJob


def _mock_sc(cloud_url: str = "postgres://cloud") -> MagicMock:
    """SiteConfig mock for post-Phase-H job.run() kwarg."""
    sc = MagicMock()
    sc.get.side_effect = lambda k, d="": (
        cloud_url if k == "database_url" else d
    )
    sc.get_bool.side_effect = lambda k, d=False: d
    sc.get_int.side_effect = lambda k, d=0: d
    return sc


def _fake_asyncpg(rows: list[dict] | None = None):
    cloud_conn = AsyncMock()
    cloud_conn.fetch = AsyncMock(return_value=rows or [])
    cloud_conn.close = AsyncMock(return_value=None)

    async def _connect(url: str) -> Any:
        return cloud_conn

    fake = MagicMock()
    fake.connect = _connect
    return fake, cloud_conn


def _row(pid: str, title: str = "t") -> dict:
    return {"id": pid, "title": title, "content": "body"}


def _mk_podcast_svc(existing: set[str], gen_result=None):
    svc = MagicMock()
    svc.episode_exists = MagicMock(side_effect=lambda pid: pid in existing)
    svc.generate_episode = AsyncMock(return_value=gen_result or MagicMock(success=True))
    return svc


@pytest.mark.unit
class TestBackfillPodcastsJobMetadata:
    def test_name(self):
        assert BackfillPodcastsJob.name == "backfill_podcasts"

    def test_schedule_4_hours(self):
        assert "4 hours" in BackfillPodcastsJob.schedule


@pytest.mark.unit
@pytest.mark.asyncio
class TestBackfillPodcastsJobRun:
    async def test_skips_when_no_database_url(self):
        job = BackfillPodcastsJob()
        sc = _mock_sc(cloud_url="")
        result = await job.run(MagicMock(), {}, site_config=sc)
        assert result.ok is True
        assert "no database_url" in result.detail

    async def test_generates_new_episodes_up_to_cap(self):
        job = BackfillPodcastsJob()
        rows = [_row(f"p{i}") for i in range(3)]
        fake_asyncpg, _ = _fake_asyncpg(rows=rows)

        svc = _mk_podcast_svc(existing=set())  # nothing exists yet

        with patch.dict("sys.modules", {"asyncpg": fake_asyncpg}), \
             patch("services.podcast_service.PodcastService", return_value=svc), \
             patch("services.r2_upload_service.upload_podcast_episode",
                   new=AsyncMock(return_value=None)):
            result = await job.run(
                MagicMock(), {"max_per_cycle": 2}, site_config=_mock_sc(),
            )

        assert result.ok is True
        # Default max_per_cycle via arg → 2 generated
        assert svc.generate_episode.await_count == 2

    async def test_skips_posts_that_already_have_episodes(self):
        job = BackfillPodcastsJob()
        rows = [_row("p1"), _row("p2")]
        fake_asyncpg, _ = _fake_asyncpg(rows=rows)

        svc = _mk_podcast_svc(existing={"p1", "p2"})  # all exist

        with patch.dict("sys.modules", {"asyncpg": fake_asyncpg}), \
             patch("services.podcast_service.PodcastService", return_value=svc), \
             patch("services.r2_upload_service.upload_podcast_episode",
                   new=AsyncMock(return_value="https://r2/p.mp3")):
            result = await job.run(MagicMock(), {}, site_config=_mock_sc())

        assert result.ok is True
        # Nothing generated because everything existed.
        svc.generate_episode.assert_not_awaited()
        # But the R2 sync pass ran — 2 existing episodes synced.
        assert "uploaded 2" in result.detail

    async def test_generation_exception_does_not_crash(self):
        job = BackfillPodcastsJob()
        rows = [_row("p1"), _row("p2")]
        fake_asyncpg, _ = _fake_asyncpg(rows=rows)

        svc = MagicMock()
        svc.episode_exists = MagicMock(return_value=False)

        call_count = 0

        async def _flaky_gen(**kwargs):
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                raise RuntimeError("TTS model OOM")
            return MagicMock(success=True)

        svc.generate_episode = _flaky_gen

        with patch.dict("sys.modules", {"asyncpg": fake_asyncpg}), \
             patch("services.podcast_service.PodcastService", return_value=svc), \
             patch("services.r2_upload_service.upload_podcast_episode",
                   new=AsyncMock(return_value="https://r2/p.mp3")):
            result = await job.run(
                MagicMock(), {"max_per_cycle": 2}, site_config=_mock_sc(),
            )

        assert result.ok is True
        # First failed, second generated.
        assert call_count == 2

    async def test_feed_rebuild_triggers_when_uploaded_count_positive(self):
        job = BackfillPodcastsJob()
        fake_asyncpg, _ = _fake_asyncpg(rows=[_row("p1")])

        svc = _mk_podcast_svc(existing={"p1"})  # already exists → sync pass uploads it

        feed_resp = MagicMock(text="<rss></rss>")
        mock_http = MagicMock()
        mock_http.get = AsyncMock(return_value=feed_resp)
        mock_http_ctx = MagicMock()
        mock_http_ctx.__aenter__ = AsyncMock(return_value=mock_http)
        mock_http_ctx.__aexit__ = AsyncMock(return_value=False)

        upload_calls = []

        async def _upload(path, key, ct):
            upload_calls.append((path, key, ct))
            return f"https://r2/{key}"

        sc = MagicMock()
        sc.get.side_effect = lambda k, d="": (
            "postgres://cloud" if k == "database_url" else "http://api"
        )
        sc.get_bool.side_effect = lambda k, d=False: d
        sc.get_int.side_effect = lambda k, d=0: d

        with patch.dict("sys.modules", {"asyncpg": fake_asyncpg}), \
             patch("services.podcast_service.PodcastService", return_value=svc), \
             patch("services.r2_upload_service.upload_podcast_episode",
                   new=AsyncMock(return_value="https://r2/ep.mp3")), \
             patch("services.r2_upload_service.upload_to_r2", new=_upload), \
             patch("httpx.AsyncClient", return_value=mock_http_ctx):
            result = await job.run(MagicMock(), {}, site_config=sc)

        assert result.ok is True
        # The feed rebuild should have pushed podcast/feed.xml.
        assert any(k == "podcast/feed.xml" for _, k, _ in upload_calls)
