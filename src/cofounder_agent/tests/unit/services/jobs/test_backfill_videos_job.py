"""Unit tests for ``services/jobs/backfill_videos.py``.

Cloud asyncpg connection + filesystem checks are mocked. Covers the
"podcast exists && video doesn't" gate and the GPU-bound max_per_cycle
cap.
"""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from services.jobs.backfill_videos import (
    BackfillVideosJob,
    _build_youtube_description,
    _dispatch_video_publishers,
    _parse_seo_keywords,
)
from services.site_config import SiteConfig


def _sc(value: Any = "", *, job_flags: dict[str, bool] | None = None) -> MagicMock:
    """Build a mock SiteConfig that returns ``value`` from any ``.get()`` call.

    Replaces the legacy ``patch("services.site_config.site_config.get", ...)``
    pattern after the job migrated to the DI seam (glad-labs-stack#330).

    ``job_flags`` (Glad-Labs/poindexter#521) maps a
    ``niche.<slug>.jobs.<job>.enabled`` key to its bool — anything absent
    defaults to enabled (``True``), matching the fail-safe helper.
    """
    job_flags = job_flags or {}
    sc = MagicMock()
    sc.get.return_value = value
    sc.get_bool.side_effect = lambda k, d=False: job_flags.get(k, True)
    return sc


# Default niche media policy used by the run tests — the baseline
# ``dev_diary`` niche opts into both video flavors + podcast so the
# niche-iteration query selects the candidate posts.
_DEFAULT_NICHE_MEDIA = ["video", "podcast"]


def _fake_niche(slug: str = "dev_diary", niche_id: str = "n-1") -> MagicMock:
    n = MagicMock()
    n.slug = slug
    n.id = niche_id
    return n


def _fake_pool(niches: list[Any] | None = None):
    """A pool stub whose ``NicheService(pool).list_active()`` resolves.

    ``NicheService`` calls ``pool.acquire()`` as an async context manager
    then ``conn.fetch("SELECT * FROM niches WHERE active ...")``. We can't
    easily route that through ``NicheService`` row parsing, so the run
    tests patch ``NicheService.list_active`` directly instead and this
    pool just needs to be a harmless MagicMock.
    """
    return MagicMock()


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


def _row(post_id: str = "abc123", title: str = "Hello") -> dict:
    return {
        "id": post_id,
        "title": title,
        "content": "Body text",
        "excerpt": "An SEO meta description.",
        "seo_keywords": "ai, automation",
        "slug": f"{post_id}-slug",
    }


@pytest.mark.unit
class TestBackfillVideosJobMetadata:
    def test_name(self):
        assert BackfillVideosJob.name == "backfill_videos"

    def test_schedule(self):
        assert "6 hours" in BackfillVideosJob.schedule

    def test_idempotent(self):
        assert BackfillVideosJob.idempotent is True


@pytest.mark.unit
@pytest.mark.asyncio
class TestBackfillVideosJobRun:
    @pytest.fixture(autouse=True)
    def _stub_active_niches(self):
        """Every run test iterates niches now (#521). Stub one active
        ``dev_diary`` niche so the niche-by-niche loop has something to
        iterate; the cloud ``fetchval`` (niche media policy) +
        ``fetch`` (posts) come from ``_fake_asyncpg``."""
        with patch(
            "services.niche_service.NicheService.list_active",
            new=AsyncMock(return_value=[_fake_niche()]),
        ):
            yield

    async def test_skips_when_no_database_url(self):
        job = BackfillVideosJob()
        result = await job.run(MagicMock(), {"_site_config": _sc("")})
        assert result.ok is True
        assert result.changes_made == 0
        assert "no database_url" in result.detail

    async def test_skips_when_asyncpg_unavailable(self):
        job = BackfillVideosJob()
        with patch.dict("sys.modules", {"asyncpg": None}):
            result = await job.run(MagicMock(), {"_site_config": _sc("postgres://cloud")})
        assert result.ok is False
        assert "asyncpg" in result.detail

    async def test_skips_post_when_podcast_missing(self, tmp_path):
        """Video generation requires a podcast — skip posts without one."""
        job = BackfillVideosJob()
        fake_asyncpg, _ = _fake_asyncpg(rows=[_row("p1")])

        with patch.dict("sys.modules", {"asyncpg": fake_asyncpg}), \
             patch("services.video_service.generate_video_for_post",
                   new=AsyncMock()) as gen_mock, \
             patch("services.podcast_service.PODCAST_DIR", tmp_path / "podcasts"), \
             patch("services.video_service.VIDEO_DIR", tmp_path / "videos"):
            (tmp_path / "podcasts").mkdir(parents=True)
            (tmp_path / "videos").mkdir(parents=True)
            result = await job.run(MagicMock(), {"_site_config": _sc("postgres://cloud")})

        assert result.ok is True
        assert result.changes_made == 0
        gen_mock.assert_not_awaited()

    async def test_skips_post_when_video_already_exists(self, tmp_path):
        job = BackfillVideosJob()
        fake_asyncpg, _ = _fake_asyncpg(rows=[_row("p1")])

        (tmp_path / "podcasts").mkdir(parents=True)
        (tmp_path / "videos").mkdir(parents=True)
        # Podcast AND video already exist → skip.
        (tmp_path / "podcasts" / "p1.mp3").write_bytes(b"fake")
        (tmp_path / "videos" / "p1.mp4").write_bytes(b"fake")

        with patch.dict("sys.modules", {"asyncpg": fake_asyncpg}), \
             patch("services.video_service.generate_video_for_post",
                   new=AsyncMock()) as gen_mock, \
             patch("services.podcast_service.PODCAST_DIR", tmp_path / "podcasts"), \
             patch("services.video_service.VIDEO_DIR", tmp_path / "videos"):
            result = await job.run(MagicMock(), {"_site_config": _sc("postgres://cloud")})

        assert result.ok is True
        assert result.changes_made == 0
        gen_mock.assert_not_awaited()

    async def test_generates_video_when_podcast_present_video_missing(self, tmp_path):
        job = BackfillVideosJob()
        fake_asyncpg, _ = _fake_asyncpg(rows=[_row("p1", "My Post")])

        (tmp_path / "podcasts").mkdir(parents=True)
        (tmp_path / "videos").mkdir(parents=True)
        (tmp_path / "podcasts" / "p1.mp3").write_bytes(b"fake podcast")

        gen_result = MagicMock(success=True)
        with patch.dict("sys.modules", {"asyncpg": fake_asyncpg}), \
             patch("services.video_service.generate_video_for_post",
                   new=AsyncMock(return_value=gen_result)) as gen_mock, \
             patch("services.podcast_service.PODCAST_DIR", tmp_path / "podcasts"), \
             patch("services.video_service.VIDEO_DIR", tmp_path / "videos"):
            result = await job.run(MagicMock(), {"_site_config": _sc("postgres://cloud")})

        assert result.ok is True
        assert result.changes_made == 1
        gen_mock.assert_awaited_once()

    async def test_max_per_cycle_caps_generation(self, tmp_path):
        job = BackfillVideosJob()
        # 3 candidate posts, all with podcasts, no videos.
        rows = [_row(f"p{i}") for i in range(3)]
        fake_asyncpg, _ = _fake_asyncpg(rows=rows)

        (tmp_path / "podcasts").mkdir(parents=True)
        (tmp_path / "videos").mkdir(parents=True)
        for i in range(3):
            (tmp_path / "podcasts" / f"p{i}.mp3").write_bytes(b"fake")

        gen_result = MagicMock(success=True)
        with patch.dict("sys.modules", {"asyncpg": fake_asyncpg}), \
             patch("services.video_service.generate_video_for_post",
                   new=AsyncMock(return_value=gen_result)) as gen_mock, \
             patch("services.podcast_service.PODCAST_DIR", tmp_path / "podcasts"), \
             patch("services.video_service.VIDEO_DIR", tmp_path / "videos"):
            # Default max_per_cycle=1 → only 1 should generate.
            result = await job.run(MagicMock(), {"_site_config": _sc("postgres://cloud")})

        assert result.ok is True
        assert result.changes_made == 1
        assert gen_mock.await_count == 1

    async def test_generation_failure_does_not_block_others(self, tmp_path):
        """If generate_video_for_post raises, the job keeps scanning."""
        job = BackfillVideosJob()
        rows = [_row(f"p{i}") for i in range(2)]
        fake_asyncpg, _ = _fake_asyncpg(rows=rows)

        (tmp_path / "podcasts").mkdir(parents=True)
        (tmp_path / "videos").mkdir(parents=True)
        for i in range(2):
            (tmp_path / "podcasts" / f"p{i}.mp3").write_bytes(b"fake")

        call_count = 0

        async def _flaky(*args, **kwargs):
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                raise RuntimeError("GPU OOM")
            return MagicMock(success=True)

        with patch.dict("sys.modules", {"asyncpg": fake_asyncpg}), \
             patch("services.video_service.generate_video_for_post", new=_flaky), \
             patch("services.podcast_service.PODCAST_DIR", tmp_path / "podcasts"), \
             patch("services.video_service.VIDEO_DIR", tmp_path / "videos"):
            # max_per_cycle=2 so both candidates are attempted.
            result = await job.run(
                MagicMock(),
                {"max_per_cycle": 2, "_site_config": _sc("postgres://cloud")},
            )

        assert result.ok is True
        # First failed, second succeeded.
        assert result.changes_made == 1
        assert call_count == 2


# --------------------------------------------------------------------------
# Per-niche enable/disable (Glad-Labs/poindexter#521)
# --------------------------------------------------------------------------


@pytest.mark.unit
@pytest.mark.asyncio
class TestBackfillVideosPerNicheToggle:
    async def test_disabled_niche_is_skipped(self, tmp_path):
        """niche.<slug>.jobs.backfill_videos.enabled=false → no posts
        queried, no generation, niche reported as skipped."""
        job = BackfillVideosJob()
        fake_asyncpg, cloud_conn = _fake_asyncpg(rows=[_row("p1")])

        (tmp_path / "podcasts").mkdir(parents=True)
        (tmp_path / "videos").mkdir(parents=True)
        (tmp_path / "podcasts" / "p1.mp3").write_bytes(b"fake")

        sc = _sc(
            "postgres://cloud",
            job_flags={"niche.dev_diary.jobs.backfill_videos.enabled": False},
        )

        with patch("services.niche_service.NicheService.list_active",
                   new=AsyncMock(return_value=[_fake_niche("dev_diary")])), \
             patch.dict("sys.modules", {"asyncpg": fake_asyncpg}), \
             patch("services.video_service.generate_video_for_post",
                   new=AsyncMock()) as gen_mock, \
             patch("services.podcast_service.PODCAST_DIR", tmp_path / "podcasts"), \
             patch("services.video_service.VIDEO_DIR", tmp_path / "videos"):
            result = await job.run(MagicMock(), {"_site_config": sc})

        assert result.ok is True
        assert result.changes_made == 0
        gen_mock.assert_not_awaited()
        # Short-circuited BEFORE touching the DB for posts.
        cloud_conn.fetch.assert_not_awaited()
        assert "skipped_niches=dev_diary" in result.detail

    async def test_absent_setting_defaults_enabled(self, tmp_path):
        """No app_settings row → niche stays enabled, posts generate."""
        job = BackfillVideosJob()
        fake_asyncpg, _ = _fake_asyncpg(rows=[_row("p1", "My Post")])

        (tmp_path / "podcasts").mkdir(parents=True)
        (tmp_path / "videos").mkdir(parents=True)
        (tmp_path / "podcasts" / "p1.mp3").write_bytes(b"fake podcast")

        # No job_flags → get_bool returns the passed default (True).
        sc = _sc("postgres://cloud")

        gen_result = MagicMock(success=True)
        with patch("services.niche_service.NicheService.list_active",
                   new=AsyncMock(return_value=[_fake_niche("dev_diary")])), \
             patch.dict("sys.modules", {"asyncpg": fake_asyncpg}), \
             patch("services.video_service.generate_video_for_post",
                   new=AsyncMock(return_value=gen_result)) as gen_mock, \
             patch("services.podcast_service.PODCAST_DIR", tmp_path / "podcasts"), \
             patch("services.video_service.VIDEO_DIR", tmp_path / "videos"):
            result = await job.run(MagicMock(), {"_site_config": sc})

        assert result.ok is True
        assert result.changes_made == 1
        gen_mock.assert_awaited_once()
        assert "skipped_niches" not in result.detail

    async def test_independent_of_podcast_flag(self, tmp_path):
        """Disabling backfill_podcasts for a niche does NOT disable
        backfill_videos — the keys are per-job."""
        job = BackfillVideosJob()
        fake_asyncpg, _ = _fake_asyncpg(rows=[_row("p1", "My Post")])

        (tmp_path / "podcasts").mkdir(parents=True)
        (tmp_path / "videos").mkdir(parents=True)
        (tmp_path / "podcasts" / "p1.mp3").write_bytes(b"fake podcast")

        sc = _sc(
            "postgres://cloud",
            job_flags={"niche.dev_diary.jobs.backfill_podcasts.enabled": False},
        )

        gen_result = MagicMock(success=True)
        with patch("services.niche_service.NicheService.list_active",
                   new=AsyncMock(return_value=[_fake_niche("dev_diary")])), \
             patch.dict("sys.modules", {"asyncpg": fake_asyncpg}), \
             patch("services.video_service.generate_video_for_post",
                   new=AsyncMock(return_value=gen_result)) as gen_mock, \
             patch("services.podcast_service.PODCAST_DIR", tmp_path / "podcasts"), \
             patch("services.video_service.VIDEO_DIR", tmp_path / "videos"):
            result = await job.run(MagicMock(), {"_site_config": sc})

        assert result.ok is True
        assert result.changes_made == 1
        gen_mock.assert_awaited_once()


# --------------------------------------------------------------------------
# YouTube SEO payload composition (glad-labs-stack#275)
# --------------------------------------------------------------------------


@pytest.mark.unit
class TestParseSeoKeywords:
    def test_parses_comma_separated(self):
        assert _parse_seo_keywords("ai, automation, gaming") == [
            "ai", "automation", "gaming",
        ]

    def test_strips_and_drops_empties(self):
        assert _parse_seo_keywords("  ai , , automation,  ") == [
            "ai", "automation",
        ]

    def test_empty_string_returns_empty_list(self):
        assert _parse_seo_keywords("") == []

    def test_caps_at_30_tags(self):
        many = ",".join(f"tag{i}" for i in range(50))
        tags = _parse_seo_keywords(many)
        assert len(tags) == 30

    def test_caps_joined_length_at_500(self):
        # 30 tags of 40 chars each → joined length far over 500; helper
        # drops trailing tags until the joined string fits.
        long_tags = ",".join("x" * 40 for _ in range(30))
        tags = _parse_seo_keywords(long_tags)
        assert len(",".join(tags)) <= 500
        assert len(tags) < 30


@pytest.mark.unit
class TestBuildYouTubeDescription:
    def _sc_with_url(self, url: str = "https://gladlabs.io") -> SiteConfig:
        return SiteConfig(initial_config={"site_url": url})

    def test_starts_with_seo_description_and_has_url_and_body(self):
        desc = _build_youtube_description(
            seo_description="Meta description here.",
            body="<p>Hello <b>world</b></p>",
            site_config=self._sc_with_url(),
            slug="my-post",
        )
        assert desc.startswith("Meta description here.")
        assert "Read the full post: https://gladlabs.io/posts/my-post" in desc
        # markup stripped from the body
        assert "Hello world" in desc
        assert "<p>" not in desc

    def test_total_stays_under_budget(self):
        big_body = "word " * 5000  # ~25k chars
        desc = _build_youtube_description(
            seo_description="Short meta.",
            body=big_body,
            site_config=self._sc_with_url(),
            slug="my-post",
        )
        assert len(desc) <= 4800

    def test_null_seo_description_no_leading_blank(self):
        desc = _build_youtube_description(
            seo_description="",
            body="Body content.",
            site_config=self._sc_with_url(),
            slug="my-post",
        )
        # No leading blank line / leading newline.
        assert not desc.startswith("\n")
        assert desc.startswith("Read the full post:")

    def test_missing_site_url_omits_backlink_no_crash(self):
        # SiteConfig with no site_url → require() raises → line omitted.
        sc = SiteConfig(initial_config={})
        desc = _build_youtube_description(
            seo_description="Meta description.",
            body="Body content.",
            site_config=sc,
            slug="my-post",
        )
        assert "Read the full post" not in desc
        assert desc.startswith("Meta description.")
        assert "Body content." in desc

    def test_missing_slug_omits_backlink(self):
        desc = _build_youtube_description(
            seo_description="Meta description.",
            body="Body content.",
            site_config=self._sc_with_url(),
            slug="",
        )
        assert "Read the full post" not in desc


@pytest.mark.unit
@pytest.mark.asyncio
class TestDispatchPayload:
    """End-to-end payload-shape assertions for _dispatch_video_publishers."""

    def _adapter_rows(self) -> list[dict]:
        return [{
            "name": "yt",
            "platform": "youtube",
            "handler_name": "youtube",
            "config": {},
            "metadata": {},
        }]

    async def _run_dispatch(self, *, seo_description, seo_keywords, slug,
                            site_config) -> dict:
        """Invoke _dispatch_video_publishers and return the captured payload."""
        # Plain object exposing only ``fetch`` (no ``acquire``) so the
        # dispatcher takes the pool.fetch branch.
        class _Pool:
            fetch = AsyncMock(return_value=self._adapter_rows())

        pool = _Pool()

        captured: dict[str, Any] = {}

        async def _fake_dispatch(surface, handler, payload, **kwargs):
            captured.update(payload)
            return {"success": True}

        fake_registry = MagicMock()
        fake_registry.dispatch = AsyncMock(side_effect=_fake_dispatch)

        fake_approval = MagicMock()
        fake_approval.is_approved = AsyncMock(return_value=True)

        with patch("services.integrations.registry", fake_registry), \
             patch("services.integrations.handlers.load_all", MagicMock()), \
             patch("services.media_approval_service", fake_approval):
            await _dispatch_video_publishers(
                pool=pool,
                site_config=site_config,
                post_id="post-1",
                video_path="/tmp/post-1.mp4",
                title="My Title",
                content="<p>Hello <b>world</b></p>",
                seo_description=seo_description,
                seo_keywords=seo_keywords,
                slug=slug,
            )
        return captured

    async def test_payload_description_and_tags(self):
        sc = SiteConfig(initial_config={"site_url": "https://gladlabs.io"})
        payload = await self._run_dispatch(
            seo_description="Great meta description.",
            seo_keywords="ai, automation, gaming",
            slug="my-post",
            site_config=sc,
        )
        assert payload["description"].startswith("Great meta description.")
        assert "https://gladlabs.io/posts/my-post" in payload["description"]
        assert "Hello world" in payload["description"]
        assert payload["tags"] == ["ai", "automation", "gaming"]
        assert payload["media_path"] == "/tmp/post-1.mp4"
        assert payload["title"] == "My Title"
        assert payload["post_id"] == "post-1"

    async def test_missing_seo_fields_tags_none_no_crash(self):
        sc = SiteConfig(initial_config={"site_url": "https://gladlabs.io"})
        payload = await self._run_dispatch(
            seo_description="",       # null excerpt
            seo_keywords="",          # null seo_keywords
            slug="my-post",
            site_config=sc,
        )
        # No leading blank line, no crash, tags omitted (None).
        assert not payload["description"].startswith("\n")
        assert payload["tags"] is None

    async def test_missing_site_url_omits_backlink(self):
        sc = SiteConfig(initial_config={})  # no site_url
        payload = await self._run_dispatch(
            seo_description="Meta desc.",
            seo_keywords="ai",
            slug="my-post",
            site_config=sc,
        )
        assert "Read the full post" not in payload["description"]
        assert payload["tags"] == ["ai"]
