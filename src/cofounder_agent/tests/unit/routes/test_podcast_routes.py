"""
Podcast Routes — Unit Tests

Tests for RSS feed generation, streaming a task's render, manual generation,
and the removal of the episode list route (Glad-Labs/poindexter#1089).
"""

from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient

from poindexter.routes.podcast_routes import (
    _build_rss_xml,
    _format_duration,
    _rfc2822,
    router,
)
from poindexter.services.site_config import SiteConfig

# storage_* cutover (#731): podcast routes read storage_public_url (was
# r2_public_url). Build a dedicated SiteConfig for the feed-rendering
# tests rather than the conftest shared singleton — the autouse
# ``_reset_singletons_between_tests`` fixture strips any key not in
# ``_TEST_BRAND_CONFIG`` from the shared instance before each test, so a
# seeded ``storage_public_url`` wouldn't survive there. This instance is
# never reset, so the feed renders media URLs instead of 503ing.
_test_site_config = SiteConfig(initial_config={
    "podcast_name": "Test Podcast",
    "podcast_description": "A test podcast feed",
    "site_url": "https://www.test-site.example.com",
    "site_domain": "test-site.example.com",
    "owner_name": "Tester",
    "owner_email": "owner@test.example.com",
    "storage_public_url": "https://pub-test-bucket.r2.dev",
})

# ---------------------------------------------------------------------------
# Test app
# ---------------------------------------------------------------------------


def _build_app():
    app = FastAPI()
    app.include_router(router)
    # storage_* cutover (#731): the feed endpoint reads storage_public_url
    # via get_site_config_dependency. Override it with the dedicated test
    # config so the route doesn't 503 on the reset-stripped shared
    # singleton.
    from poindexter.utils.route_utils import get_site_config_dependency
    app.dependency_overrides[get_site_config_dependency] = lambda: _test_site_config
    return app


app = _build_app()
client = TestClient(app, raise_server_exceptions=False)


# ---------------------------------------------------------------------------
# Helper tests
# ---------------------------------------------------------------------------


class TestFormatDuration:
    def test_zero(self):
        assert _format_duration(0) == "00:00:00"

    def test_seconds_only(self):
        assert _format_duration(45) == "00:00:45"

    def test_minutes_and_seconds(self):
        assert _format_duration(125) == "00:02:05"

    def test_hours_minutes_seconds(self):
        assert _format_duration(3661) == "01:01:01"

    def test_large_value(self):
        assert _format_duration(7200) == "02:00:00"

    def test_exact_hour(self):
        assert _format_duration(3600) == "01:00:00"


class TestRfc2822:
    def test_formats_utc_datetime(self):
        dt = datetime(2026, 4, 5, 14, 30, 0, tzinfo=timezone.utc)
        result = _rfc2822(dt)
        assert "05 Apr 2026" in result
        assert "14:30:00 +0000" in result

    def test_formats_midnight(self):
        dt = datetime(2026, 1, 1, 0, 0, 0, tzinfo=timezone.utc)
        result = _rfc2822(dt)
        assert "01 Jan 2026" in result
        assert "00:00:00" in result


# ---------------------------------------------------------------------------
# RSS feed XML builder
# ---------------------------------------------------------------------------


class TestBuildRssXml:
    def test_empty_episodes(self):
        xml = _build_rss_xml([], _test_site_config)
        assert '<?xml version="1.0"' in xml
        assert "<channel>" in xml
        assert "<title>Test Podcast</title>" in xml
        assert "<item>" not in xml

    def test_single_episode(self):
        episodes = [{
            "post_id": "123",
            "title": "Test Episode",
            "slug": "test-episode",
            "description": "A test",
            "published_at": datetime(2026, 4, 1, 12, 0, 0, tzinfo=timezone.utc),
            "file_size_bytes": 5000000,
            "duration_seconds": 300,
        }]
        xml = _build_rss_xml(episodes, _test_site_config)
        assert "<item>" in xml
        assert "<title>Test Episode</title>" in xml
        assert "test-site.example.com-podcast-123" in xml
        assert "audio/mpeg" in xml
        assert "5000000" in xml

    def test_episode_emits_itunes_summary_and_keywords(self):
        episodes = [{
            "post_id": "123",
            "title": "Test Episode",
            "slug": "test-episode",
            "description": "An SEO meta description.",
            "keywords": "ai, automation, pipelines",
            "published_at": datetime(2026, 4, 1, 12, 0, 0, tzinfo=timezone.utc),
            "file_size_bytes": 5000000,
            "duration_seconds": 300,
        }]
        xml = _build_rss_xml(episodes, _test_site_config)
        # itunes:summary mirrors the SEO description (what Apple/Spotify show).
        assert "An SEO meta description." in xml
        assert "summary" in xml
        # itunes:keywords carries the comma-joined SEO keywords.
        assert "ai, automation, pipelines" in xml
        assert "keywords" in xml

    def test_episode_omits_keywords_when_empty(self):
        episodes = [{
            "post_id": "123",
            "title": "Test Episode",
            "slug": "test-episode",
            "description": "Body.",
            "keywords": "",
            "published_at": datetime(2026, 4, 1, 12, 0, 0, tzinfo=timezone.utc),
            "file_size_bytes": 5000000,
            "duration_seconds": 300,
        }]
        xml = _build_rss_xml(episodes, _test_site_config)
        # itunes:summary still present; keywords element omitted entirely.
        assert "summary" in xml
        assert "<itunes:keywords>" not in xml and "}keywords>" not in xml

    def test_episode_without_duration(self):
        episodes = [{
            "post_id": "456",
            "title": "No Duration",
            "slug": "no-duration",
            "description": "",
            "published_at": None,
            "file_size_bytes": 1000,
            "duration_seconds": 0,
        }]
        xml = _build_rss_xml(episodes, _test_site_config)
        assert "<item>" in xml
        # No pubDate if published_at is None
        assert "pubDate" not in xml

    def test_episode_with_string_date(self):
        episodes = [{
            "post_id": "789",
            "title": "String Date",
            "slug": "string-date",
            "description": "",
            "published_at": "2026-04-01T12:00:00+00:00",
            "file_size_bytes": 1000,
            "duration_seconds": 0,
        }]
        xml = _build_rss_xml(episodes, _test_site_config)
        assert "pubDate" in xml

    def test_multiple_episodes(self):
        episodes = [
            {
                "post_id": str(i),
                "title": f"Episode {i}",
                "slug": f"ep-{i}",
                "description": "",
                "published_at": None,
                "file_size_bytes": 1000,
                "duration_seconds": 0,
            }
            for i in range(3)
        ]
        xml = _build_rss_xml(episodes, _test_site_config)
        assert xml.count("<item>") == 3

    def test_enclosure_uses_the_stamped_asset_url(self):
        episodes = [{
            "post_id": "123", "title": "T", "slug": "t", "description": "",
            "published_at": None, "file_size_bytes": 1, "duration_seconds": 0,
            "enclosure_url": "https://cdn.example/podcast/v2/123.mp3",
        }]
        xml = _build_rss_xml(episodes, _test_site_config)
        assert 'url="https://cdn.example/podcast/v2/123.mp3"' in xml

    def test_enclosure_falls_back_to_the_delivery_key(self):
        """An approved episode whose row has no URL is served from the key
        podcast_distribute uploads to, spelled once in ``podcast_episode_key``
        (podcast_cdn_version defaults to v2)."""
        from poindexter.services.r2_upload_service import podcast_episode_key

        episodes = [{
            "post_id": "123", "title": "T", "slug": "t", "description": "",
            "published_at": None, "file_size_bytes": 1, "duration_seconds": 0,
            "enclosure_url": "",
        }]
        xml = _build_rss_xml(episodes, _test_site_config)
        expected = f"https://pub-test-bucket.r2.dev/{podcast_episode_key('123', 'v2')}"
        assert f'url="{expected}"' in xml
        assert expected.endswith("/podcast/v2/123.mp3")


class TestBuildRssXmlWithoutStoragePublicUrl:
    """poindexter#485: the feed never guesses a bucket. With an episode to list
    and ``storage_public_url`` unset, building the feed answers 503 naming the
    setting instead of emitting enclosures under a bucket nobody configured."""

    def test_an_unstamped_episode_503s_naming_the_setting(self):
        unconfigured = SiteConfig(initial_config={
            "podcast_name": "Test Podcast",
            "podcast_description": "A test podcast feed",
            "site_url": "https://www.test-site.example.com",
            "site_domain": "test-site.example.com",
            "owner_name": "Tester",
            "owner_email": "owner@test.example.com",
        })
        episodes = [{
            "post_id": "123",
            "title": "Needs the fallback key",
            "slug": "needs-the-fallback-key",
            "description": "",
            "published_at": datetime(2026, 9, 1, tzinfo=timezone.utc),
            "file_size_bytes": 1,
            "duration_seconds": 60,
            "enclosure_url": "",
        }]

        with pytest.raises(HTTPException) as exc:
            _build_rss_xml(episodes, unconfigured)

        assert exc.value.status_code == 503
        assert "storage_public_url" in exc.value.detail


# ---------------------------------------------------------------------------
# GET /api/podcast/feed.xml
# ---------------------------------------------------------------------------


class TestPodcastFeed:
    @patch("poindexter.utils.route_utils.get_services")
    def test_empty_feed_when_no_episodes(self, mock_gs):
        mock_db = MagicMock()
        mock_db.pool = None
        mock_gs.return_value.get_database.return_value = mock_db

        resp = client.get("/api/podcast/feed.xml")
        assert resp.status_code == 200
        assert "application/rss+xml" in resp.headers["content-type"]
        assert "<item>" not in resp.text

    @patch("poindexter.utils.route_utils.get_services")
    def test_feed_lists_episode_from_media_assets(self, mock_gs):
        """The feed sources the enclosure from media_assets (type='podcast'),
        not a local-disk scan — so atom-produced (task-keyed) episodes surface."""
        from datetime import datetime, timezone

        mock_conn = AsyncMock()
        mock_conn.fetch.return_value = [
            {
                "post_id": "11111111-1111-1111-1111-111111111111",
                "title": "Ep One",
                "slug": "ep-one",
                "excerpt": "An episode.",
                "seo_keywords": "ai,ml",
                "published_at": datetime(2026, 6, 1, tzinfo=timezone.utc),
                "url": "https://cdn.example.com/podcast/v2/ep1.mp3",
                "file_size_bytes": 12345,
                "duration_ms": 600000,
            }
        ]
        ctx = MagicMock()
        ctx.__aenter__ = AsyncMock(return_value=mock_conn)
        ctx.__aexit__ = AsyncMock(return_value=False)
        mock_pool = MagicMock()
        mock_pool.acquire.return_value = ctx
        mock_db = MagicMock()
        mock_db.pool = mock_pool
        mock_gs.return_value.get_database.return_value = mock_db

        resp = client.get("/api/podcast/feed.xml")
        assert resp.status_code == 200
        assert "<item>" in resp.text
        assert "Ep One" in resp.text
        assert "https://cdn.example.com/podcast/v2/ep1.mp3" in resp.text

    def test_feed_sql_filters_on_media_to_generate(self):
        """The RSS feed must only list posts that opted into podcasts.

        Pinned per ``feedback_filter_on_seams_not_slugs``: the canonical
        seam is ``posts.media_to_generate`` populated at publish time
        from ``niches.default_media_to_generate``. dev_diary's policy
        is ``{}``, so dev_diary posts must NOT appear in the feed even
        when an orphan MP3 still sits on disk from before the per-niche
        policy landed (2026-05-19).

        Slug-pattern filtering (``slug NOT LIKE 'what-we-shipped%'``)
        is the hack Matt rejected — this test catches a regression to
        either no filter at all OR slug-based filter.
        """
        import inspect

        from poindexter.routes.podcast_routes import podcast_feed

        source = inspect.getsource(podcast_feed)
        assert "'podcast' = ANY(media_to_generate)" in source, (
            "RSS feed query must filter on media_to_generate — see "
            "feedback_filter_on_seams_not_slugs and the per-niche "
            "policy column added in migration "
            "20260519_134736_niches_default_media_to_generate.py."
        )
        # Defense against slug-based regressions.
        assert "what-we-shipped" not in source, (
            "Don't filter by slug pattern — use the media_to_generate "
            "array on posts."
        )
        assert "NOT LIKE" not in source.upper().replace("NOT NULL", ""), (
            "SQL LIKE/NOT LIKE on slug is the rejected hack — filter "
            "on the canonical media_to_generate seam instead."
        )

    def test_feed_sql_filters_on_media_approval(self):
        """RSS feed must filter on the per-medium operator approval.

        Per ``feedback_human_approval``, audio reaching Apple Podcasts
        / Spotify needs an operator decision behind it. The
        ``media_approvals`` table (migration 20260527_233118) is the
        canonical gate — a missing row OR ``status != 'approved'`` means
        the episode stays off the feed.

        Pinned to catch regressions where someone removes the gate to
        "fix" a missing episode without realizing why the gate is
        there. The right fix is approve the row, not strip the filter.
        """
        import inspect

        from poindexter.routes.podcast_routes import podcast_feed

        source = inspect.getsource(podcast_feed)
        assert "media_approvals" in source, (
            "RSS feed query must reference media_approvals — see "
            "feedback_human_approval and "
            "services/media_approval_service.py."
        )
        assert "ma.status = 'approved'" in source, (
            "RSS feed query must require status='approved' — pending "
            "/ rejected media must NOT reach Apple/Spotify."
        )
        assert "ma.medium = 'podcast'" in source, (
            "RSS feed query must scope the approval check to the "
            "podcast medium (not video / video_short)."
        )


# ---------------------------------------------------------------------------
# GET /api/podcast/episodes/{task_id}.mp3
# ---------------------------------------------------------------------------

# A pipeline task id: podcast.persist names every render ``{task_id}.mp3``.
TASK_ID = "7c9e6679-7425-40de-944b-e07fc1f90ae7"


class TestStreamEpisode:
    def test_route_is_keyed_by_task_id(self):
        """The URL shape the operator console builds from ``t.task_id``
        (console/js/app.jsx) — the parameter says what the file name is."""
        paths = [route.path for route in router.routes]
        assert "/api/podcast/episodes/{task_id}.mp3" in paths
        assert "/api/podcast/episodes/{post_id}.mp3" not in paths

    def test_missing_episode_returns_404(self):
        with patch("poindexter.routes.podcast_routes.PODCAST_DIR", Path("/nonexistent/path")):
            resp = client.get(f"/api/podcast/episodes/{TASK_ID}.mp3")
            assert resp.status_code == 404

    def test_path_traversal_blocked(self):
        with patch("poindexter.routes.podcast_routes.PODCAST_DIR", Path("/tmp/podcasts")):
            resp = client.get("/api/podcast/episodes/..%2F..%2Fetc%2Fpasswd.mp3")
            assert resp.status_code == 404

    def test_serves_the_file_the_task_rendered(self, tmp_path):
        mp3_file = tmp_path / f"{TASK_ID}.mp3"
        mp3_file.write_bytes(b"\xff\xfb\x90\x00" * 100)

        with patch("poindexter.routes.podcast_routes.PODCAST_DIR", tmp_path):
            resp = client.get(f"/api/podcast/episodes/{TASK_ID}.mp3")
            assert resp.status_code == 200
            assert resp.headers["content-type"] == "audio/mpeg"
            assert resp.content == mp3_file.read_bytes()

    def test_file_lookup_does_not_consult_media_assets(self, tmp_path):
        """A second render of the same post leaves the post's one podcast row
        carrying the FIRST task's id, so only the file name says which file a
        task rendered. Serving it must not need the DB at all."""
        (tmp_path / f"{TASK_ID}.mp3").write_bytes(b"\xff\xfb\x90\x00" * 10)
        with patch("poindexter.routes.podcast_routes.PODCAST_DIR", tmp_path), \
             patch("poindexter.utils.route_utils.get_services") as mock_gs:
            resp = client.get(f"/api/podcast/episodes/{TASK_ID}.mp3")
        assert resp.status_code == 200
        mock_gs.assert_not_called()


# ---------------------------------------------------------------------------
# Retired: GET /api/podcast/episodes
# ---------------------------------------------------------------------------


class TestRetiredEpisodeList:
    """Glad-Labs/poindexter#1089: the list scanned the podcast dir and labelled
    each file stem ``post_id``. Since the task-keyed cutover (#1460) the stems
    are task ids, mixed with pre-cutover post ids and ``{post_id}-narration``
    siblings. Nothing called it. The feed lists approved episodes and the
    console's ``/api/media-approval/{post_id}/podcast/preview`` streams pending
    ones, both through ``media_assets``. Don't bring it back as a directory
    scan.

    Asserted on the router: a GET of the bare path could otherwise be taken
    for a missing-file 404 from a route that still exists.
    """

    def test_no_list_route_is_registered(self):
        paths = [route.path for route in router.routes]
        assert "/api/podcast/feed.xml" in paths
        assert "/api/podcast/episodes" not in paths, paths

    def test_the_service_no_longer_lists_the_directory(self):
        from poindexter.services.podcast_service import PodcastService

        assert not hasattr(PodcastService, "list_episodes")


# ---------------------------------------------------------------------------
# POST /api/podcast/generate/{post_id}
# ---------------------------------------------------------------------------


class TestGenerateEpisode:
    def _make_app_with_auth_override(self):
        """Build app with auth bypassed."""
        from middleware.api_token_auth import verify_api_token

        test_app = FastAPI()
        test_app.include_router(router)
        test_app.dependency_overrides[verify_api_token] = lambda: None
        return TestClient(test_app, raise_server_exceptions=False)

    @patch("poindexter.utils.route_utils.get_services")
    def test_no_db_returns_503(self, mock_gs):
        tc = self._make_app_with_auth_override()
        mock_db = MagicMock()
        mock_db.pool = None
        mock_gs.return_value.get_database.return_value = mock_db

        resp = tc.post("/api/podcast/generate/abc123")
        assert resp.status_code == 503

    @patch("poindexter.utils.route_utils.get_services")
    def test_post_not_found_returns_404(self, mock_gs):
        tc = self._make_app_with_auth_override()
        mock_conn = AsyncMock()
        mock_conn.fetchrow.return_value = None

        ctx = MagicMock()
        ctx.__aenter__ = AsyncMock(return_value=mock_conn)
        ctx.__aexit__ = AsyncMock(return_value=False)
        mock_pool = MagicMock()
        mock_pool.acquire.return_value = ctx

        mock_db = MagicMock()
        mock_db.pool = mock_pool
        mock_gs.return_value.get_database.return_value = mock_db

        resp = tc.post("/api/podcast/generate/nonexistent")
        assert resp.status_code == 404

    @patch("poindexter.routes.podcast_routes.PodcastService")
    @patch("poindexter.utils.route_utils.get_services")
    def test_successful_generation(self, mock_gs, mock_svc_cls):
        tc = self._make_app_with_auth_override()

        mock_conn = AsyncMock()
        mock_conn.fetchrow.return_value = {
            "id": "post-1",
            "title": "Test Post",
            "content": "Some content here",
        }

        ctx = MagicMock()
        ctx.__aenter__ = AsyncMock(return_value=mock_conn)
        ctx.__aexit__ = AsyncMock(return_value=False)
        mock_pool = MagicMock()
        mock_pool.acquire.return_value = ctx

        mock_db = MagicMock()
        mock_db.pool = mock_pool
        mock_gs.return_value.get_database.return_value = mock_db

        mock_result = MagicMock()
        mock_result.success = True
        mock_result.file_path = "/tmp/post-1.mp3"
        mock_result.duration_seconds = 120
        mock_result.file_size_bytes = 50000

        mock_svc = MagicMock()
        mock_svc.generate_episode = AsyncMock(return_value=mock_result)
        mock_svc_cls.return_value = mock_svc

        resp = tc.post("/api/podcast/generate/post-1")
        assert resp.status_code == 200
        data = resp.json()
        assert data["success"] is True
        assert data["post_id"] == "post-1"

    @patch("poindexter.routes.podcast_routes.PodcastService")
    @patch("poindexter.utils.route_utils.get_services")
    def test_generation_failure_returns_500(self, mock_gs, mock_svc_cls):
        tc = self._make_app_with_auth_override()

        mock_conn = AsyncMock()
        mock_conn.fetchrow.return_value = {
            "id": "post-1",
            "title": "Test Post",
            "content": "Content",
        }

        ctx = MagicMock()
        ctx.__aenter__ = AsyncMock(return_value=mock_conn)
        ctx.__aexit__ = AsyncMock(return_value=False)
        mock_pool = MagicMock()
        mock_pool.acquire.return_value = ctx

        mock_db = MagicMock()
        mock_db.pool = mock_pool
        mock_gs.return_value.get_database.return_value = mock_db

        mock_result = MagicMock()
        mock_result.success = False
        mock_result.error = "TTS engine unavailable"

        mock_svc = MagicMock()
        mock_svc.generate_episode = AsyncMock(return_value=mock_result)
        mock_svc_cls.return_value = mock_svc

        resp = tc.post("/api/podcast/generate/post-1")
        assert resp.status_code == 500
