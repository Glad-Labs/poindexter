"""Unit tests for ``services/jobs/backfill_podcasts.py``.

Covers the two-pass (R2 sync + generation) workflow plus the
RSS-feed-rebuild side effect. Podcast + R2 services are mocked.

R2 mocking shape (post DI-PR-4 migration): the job constructs
``R2UploadService(site_config=sc)`` inline; tests patch
``services.r2_upload_service.R2UploadService`` to return a MagicMock
with AsyncMock methods.
"""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from services.jobs.backfill_podcasts import BackfillPodcastsJob


def _sc(
    database_url: str = "",
    api_base: str = "http://test",
    *,
    job_flags: dict[str, bool] | None = None,
) -> MagicMock:
    """Mock SiteConfig — replaces patch("services.site_config.site_config.get").

    Job migrated to DI seam in glad-labs-stack#330; tests pass site_config
    via the config dict instead.

    ``job_flags`` (Glad-Labs/poindexter#521) maps a
    ``niche.<slug>.jobs.<job>.enabled`` key to its bool — anything absent
    defaults to enabled (``True``), matching the fail-safe helper.
    """
    job_flags = job_flags or {}
    sc = MagicMock()
    sc.get.side_effect = lambda k, d="": (
        database_url if k == "database_url" else
        api_base if k == "internal_api_base_url" else d
    )
    sc.get_bool.side_effect = lambda k, d=False: job_flags.get(k, True)
    return sc


# Baseline ``dev_diary`` niche opts into podcast + a video flavor so the
# niche-iteration query selects the candidate posts.
_DEFAULT_NICHE_MEDIA = ["podcast", "video"]


def _fake_niche(slug: str = "dev_diary", niche_id: str = "n-1") -> MagicMock:
    n = MagicMock()
    n.slug = slug
    n.id = niche_id
    return n


def _fake_asyncpg(
    rows: list[dict] | None = None,
    niche_media: list[str] | None = None,
):
    cloud_conn = AsyncMock()
    cloud_conn.fetch = AsyncMock(return_value=rows or [])
    cloud_conn.fetchval = AsyncMock(
        return_value=niche_media if niche_media is not None else _DEFAULT_NICHE_MEDIA
    )
    cloud_conn.close = AsyncMock(return_value=None)

    async def _connect(url: str) -> Any:
        return cloud_conn

    fake = MagicMock()
    fake.connect = _connect
    return fake, cloud_conn


def _row(pid: str, title: str = "t") -> dict:
    # ``excerpt`` + ``seo_keywords`` mirror the SELECT in backfill_podcasts.py
    # (Glad-Labs/poindexter#539) so the generate_episode call's SEO parity
    # args (seo_description=row["excerpt"], seo_keywords=row["seo_keywords"])
    # resolve against the fixture.
    return {
        "id": pid,
        "title": title,
        "content": "body",
        "excerpt": "An SEO meta description.",
        "seo_keywords": "ai, automation",
    }


def _mk_podcast_svc(existing: set[str], gen_result=None):
    svc = MagicMock()
    svc.episode_exists = MagicMock(side_effect=lambda pid: pid in existing)
    svc.generate_episode = AsyncMock(return_value=gen_result or MagicMock(success=True))
    return svc


def _mk_r2_svc(
    *,
    upload_podcast_return: Any = None,
    upload_to_r2_return: Any = "https://r2/feed.xml",
) -> MagicMock:
    """Build a MagicMock standing in for an R2UploadService instance."""
    r2 = MagicMock()
    r2.upload_podcast_episode = AsyncMock(return_value=upload_podcast_return)
    r2.upload_video_episode = AsyncMock(return_value=None)
    r2.upload_to_r2 = AsyncMock(return_value=upload_to_r2_return)
    return r2


@pytest.mark.unit
class TestBackfillPodcastsJobMetadata:
    def test_name(self):
        assert BackfillPodcastsJob.name == "backfill_podcasts"

    def test_schedule_4_hours(self):
        assert "4 hours" in BackfillPodcastsJob.schedule


@pytest.mark.unit
@pytest.mark.asyncio
class TestBackfillPodcastsJobRun:
    @pytest.fixture(autouse=True)
    def _stub_active_niches(self):
        """Every run test iterates niches now (#521). Stub one active
        ``dev_diary`` niche so the niche-by-niche loop has something to
        iterate; the cloud ``fetchval`` (niche media policy) + ``fetch``
        (posts) come from ``_fake_asyncpg``."""
        with patch(
            "services.niche_service.NicheService.list_active",
            new=AsyncMock(return_value=[_fake_niche()]),
        ):
            yield

    async def test_skips_when_no_database_url(self):
        job = BackfillPodcastsJob()
        result = await job.run(MagicMock(), {"_site_config": _sc("")})
        assert result.ok is True
        assert "no database_url" in result.detail

    async def test_generates_new_episodes_up_to_cap(self):
        job = BackfillPodcastsJob()
        rows = [_row(f"p{i}") for i in range(3)]
        fake_asyncpg, _ = _fake_asyncpg(rows=rows)

        svc = _mk_podcast_svc(existing=set())  # nothing exists yet
        r2 = _mk_r2_svc(upload_podcast_return=None)

        with patch.dict("sys.modules", {"asyncpg": fake_asyncpg}), \
             patch("services.podcast_service.PodcastService", return_value=svc), \
             patch("services.r2_upload_service.R2UploadService", return_value=r2):
            result = await job.run(
                MagicMock(),
                {"max_per_cycle": 2, "_site_config": _sc("postgres://cloud")},
            )

        assert result.ok is True
        # Default max_per_cycle via arg → 2 generated
        assert svc.generate_episode.await_count == 2

    async def test_skips_posts_that_already_have_episodes(self):
        job = BackfillPodcastsJob()
        rows = [_row("p1"), _row("p2")]
        fake_asyncpg, _ = _fake_asyncpg(rows=rows)

        svc = _mk_podcast_svc(existing={"p1", "p2"})  # all exist
        r2 = _mk_r2_svc(upload_podcast_return="https://r2/p.mp3")

        with patch.dict("sys.modules", {"asyncpg": fake_asyncpg}), \
             patch("services.podcast_service.PodcastService", return_value=svc), \
             patch("services.r2_upload_service.R2UploadService", return_value=r2):
            result = await job.run(MagicMock(), {"_site_config": _sc("postgres://cloud")})

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
        r2 = _mk_r2_svc(upload_podcast_return="https://r2/p.mp3")

        with patch.dict("sys.modules", {"asyncpg": fake_asyncpg}), \
             patch("services.podcast_service.PodcastService", return_value=svc), \
             patch("services.r2_upload_service.R2UploadService", return_value=r2):
            result = await job.run(
                MagicMock(),
                {"max_per_cycle": 2, "_site_config": _sc("postgres://cloud")},
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

        r2 = MagicMock()
        r2.upload_podcast_episode = AsyncMock(return_value="https://r2/ep.mp3")
        r2.upload_video_episode = AsyncMock(return_value=None)
        r2.upload_to_r2 = AsyncMock(side_effect=_upload)

        with patch.dict("sys.modules", {"asyncpg": fake_asyncpg}), \
             patch("services.podcast_service.PodcastService", return_value=svc), \
             patch("services.r2_upload_service.R2UploadService", return_value=r2), \
             patch("httpx.AsyncClient", return_value=mock_http_ctx):
            result = await job.run(
                MagicMock(),
                {"_site_config": _sc("postgres://cloud", api_base="http://api")},
            )

        assert result.ok is True
        # The feed rebuild should have pushed podcast/feed.xml.
        assert any(k == "podcast/feed.xml" for _, k, _ in upload_calls)


# --------------------------------------------------------------------------
# Per-niche enable/disable (Glad-Labs/poindexter#521)
# --------------------------------------------------------------------------


@pytest.mark.unit
@pytest.mark.asyncio
class TestBackfillPodcastsPerNicheToggle:
    async def test_disabled_niche_is_skipped(self):
        """niche.<slug>.jobs.backfill_podcasts.enabled=false → no posts
        queried, no generation, niche reported as skipped."""
        job = BackfillPodcastsJob()
        fake_asyncpg, cloud_conn = _fake_asyncpg(rows=[_row("p1")])

        svc = _mk_podcast_svc(existing=set())
        r2 = _mk_r2_svc(upload_podcast_return=None)

        sc = _sc(
            "postgres://cloud",
            job_flags={"niche.dev_diary.jobs.backfill_podcasts.enabled": False},
        )

        with patch("services.niche_service.NicheService.list_active",
                   new=AsyncMock(return_value=[_fake_niche("dev_diary")])), \
             patch.dict("sys.modules", {"asyncpg": fake_asyncpg}), \
             patch("services.podcast_service.PodcastService", return_value=svc), \
             patch("services.r2_upload_service.R2UploadService", return_value=r2):
            result = await job.run(MagicMock(), {"_site_config": sc})

        assert result.ok is True
        svc.generate_episode.assert_not_awaited()
        # Short-circuited BEFORE touching the DB for posts.
        cloud_conn.fetch.assert_not_awaited()
        assert "skipped_niches=dev_diary" in result.detail

    async def test_absent_setting_defaults_enabled(self):
        """No app_settings row → niche stays enabled, episodes generate."""
        job = BackfillPodcastsJob()
        rows = [_row(f"p{i}") for i in range(2)]
        fake_asyncpg, _ = _fake_asyncpg(rows=rows)

        svc = _mk_podcast_svc(existing=set())
        r2 = _mk_r2_svc(upload_podcast_return=None)

        # No job_flags → get_bool returns the passed default (True).
        sc = _sc("postgres://cloud")

        with patch("services.niche_service.NicheService.list_active",
                   new=AsyncMock(return_value=[_fake_niche("dev_diary")])), \
             patch.dict("sys.modules", {"asyncpg": fake_asyncpg}), \
             patch("services.podcast_service.PodcastService", return_value=svc), \
             patch("services.r2_upload_service.R2UploadService", return_value=r2):
            result = await job.run(
                MagicMock(),
                {"max_per_cycle": 2, "_site_config": sc},
            )

        assert result.ok is True
        assert svc.generate_episode.await_count == 2
        assert "skipped_niches" not in result.detail

    async def test_independent_of_video_flag(self):
        """Disabling backfill_videos for a niche does NOT disable
        backfill_podcasts — the keys are per-job."""
        job = BackfillPodcastsJob()
        fake_asyncpg, _ = _fake_asyncpg(rows=[_row("p1")])

        svc = _mk_podcast_svc(existing=set())
        r2 = _mk_r2_svc(upload_podcast_return=None)

        sc = _sc(
            "postgres://cloud",
            job_flags={"niche.dev_diary.jobs.backfill_videos.enabled": False},
        )

        with patch("services.niche_service.NicheService.list_active",
                   new=AsyncMock(return_value=[_fake_niche("dev_diary")])), \
             patch.dict("sys.modules", {"asyncpg": fake_asyncpg}), \
             patch("services.podcast_service.PodcastService", return_value=svc), \
             patch("services.r2_upload_service.R2UploadService", return_value=r2):
            result = await job.run(MagicMock(), {"_site_config": sc})

        assert result.ok is True
        svc.generate_episode.assert_awaited()
        assert "skipped_niches" not in result.detail
