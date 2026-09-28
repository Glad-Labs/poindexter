"""
Video Routes — Unit Tests

Tests for the video RSS feed, and for the removal of the episode list and
stream routes (Glad-Labs/poindexter#1087).
"""

from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock, patch

from fastapi import FastAPI
from fastapi.testclient import TestClient

from poindexter.routes.video_routes import _rfc2822, router
from poindexter.services.site_config import SiteConfig

# storage_* cutover (#731): video routes read storage_public_url (was
# r2_public_url). Build a dedicated SiteConfig for the feed tests rather
# than the conftest shared singleton — the autouse
# ``_reset_singletons_between_tests`` fixture strips any key not in
# ``_TEST_BRAND_CONFIG`` from the shared instance before each test, so a
# seeded ``storage_public_url`` wouldn't survive there. Override the DI
# dependency with this instance so the feed renders media URLs instead
# of 503ing.
_test_site_config = SiteConfig(initial_config={
    "video_feed_name": "Test Video",
    "site_url": "https://www.test-site.example.com",
    "site_domain": "test-site.example.com",
    "storage_public_url": "https://pub-test-bucket.r2.dev",
})

# ---------------------------------------------------------------------------
# Test app
# ---------------------------------------------------------------------------


def _build_app():
    app = FastAPI()
    app.include_router(router)
    from poindexter.utils.route_utils import get_site_config_dependency
    app.dependency_overrides[get_site_config_dependency] = lambda: _test_site_config
    return app


app = _build_app()
client = TestClient(app, raise_server_exceptions=False)


# ---------------------------------------------------------------------------
# Helper tests
# ---------------------------------------------------------------------------


class TestRfc2822:
    def test_formats_utc_datetime(self):
        dt = datetime(2026, 4, 5, 14, 30, 0, tzinfo=timezone.utc)
        result = _rfc2822(dt)
        assert "05 Apr 2026" in result
        assert "14:30:00 +0000" in result


# ---------------------------------------------------------------------------
# Retired: GET /api/video/episodes and GET /api/video/episodes/{post_id}.mp4
# ---------------------------------------------------------------------------


class TestRetiredEpisodeRoutes:
    """Glad-Labs/poindexter#1087: both routes scanned the video dir for
    ``{post_id}.mp4``. Since the task-keyed cutover (#1460) every render there
    is ``{task_id}.mp4`` / ``{task_id}_short.mp4``, so the list mislabelled task
    ids as post ids (and listed Shorts as episodes) and the stream 404ed for
    every video. Nothing called either one. The feed's enclosures and the
    console's ``/api/media-approval/{post_id}/video/preview`` cover them, both
    sourced from ``media_assets``. Don't bring them back as a directory scan.

    Asserted on the router, not by status code: the old stream handler also
    answered 404 (for a missing file), so a 404 can't tell the two apart.
    """

    def test_no_episode_routes_are_registered(self):
        paths = [route.path for route in router.routes]
        assert "/api/video/feed.xml" in paths
        assert not [p for p in paths if p.startswith("/api/video/episodes")], paths


# ---------------------------------------------------------------------------
# GET /api/video/feed.xml
# ---------------------------------------------------------------------------


class TestVideoFeed:
    @patch("poindexter.utils.route_utils.get_services")
    def test_empty_feed_when_no_videos(self, mock_gs):
        mock_db = MagicMock()
        mock_db.pool = None
        mock_db.cloud_pool = None
        mock_gs.return_value.get_database.return_value = mock_db

        resp = client.get("/api/video/feed.xml")
        assert resp.status_code == 200
        assert "application/rss+xml" in resp.headers["content-type"]
        assert "<item>" not in resp.text
        assert "Test Video" in resp.text

    @patch("poindexter.utils.route_utils.get_services")
    def test_feed_renders_approved_episodes(self, mock_gs):
        """The feed renders the rows the (gated) query returns, sourced from
        media_assets like the podcast feed — enclosure uses the asset row's
        R2 url, length from file_size_bytes."""
        mock_conn = AsyncMock()
        mock_conn.fetch.return_value = [
            {
                "post_id": "post-1",
                "title": "Test Video",
                "slug": "test-video",
                "excerpt": "A test video",
                "published_at": datetime(2026, 4, 1, 12, 0, 0, tzinfo=timezone.utc),
                "url": "https://pub-test-bucket.r2.dev/video/post-1.mp4",
                "file_size_bytes": 5000,
            }
        ]

        ctx = MagicMock()
        ctx.__aenter__ = AsyncMock(return_value=mock_conn)
        ctx.__aexit__ = AsyncMock(return_value=False)
        mock_pool = MagicMock()
        mock_pool.acquire.return_value = ctx

        mock_db = MagicMock()
        mock_db.cloud_pool = mock_pool
        mock_gs.return_value.get_database.return_value = mock_db

        resp = client.get("/api/video/feed.xml")
        assert resp.status_code == 200
        assert "<item>" in resp.text
        assert "Test Video" in resp.text
        assert "video/mp4" in resp.text
        # Enclosure uses the asset-row R2 url (not a disk path).
        assert "https://pub-test-bucket.r2.dev/video/post-1.mp4" in resp.text

    @patch("poindexter.utils.route_utils.get_services")
    def test_feed_query_requires_approved_media_approval(self, mock_gs):
        """The video feed MUST gate on an approved media_approvals row
        (medium='video') joined to a video media_assets row — mirroring the
        podcast feed and the operator requirement that ALL media is gated
        before any public surface (the approval gate covering every medium).

        A mock can't exercise a real JOIN, so we pin the gate in the query
        text — the same SQL-shape contract the podcast/reconciliation tests
        use.
        """
        captured: list[str] = []

        async def _fetch(sql, *_a, **_kw):
            captured.append(sql)
            return []

        mock_conn = AsyncMock()
        mock_conn.fetch = AsyncMock(side_effect=_fetch)
        ctx = MagicMock()
        ctx.__aenter__ = AsyncMock(return_value=mock_conn)
        ctx.__aexit__ = AsyncMock(return_value=False)
        mock_pool = MagicMock()
        mock_pool.acquire.return_value = ctx
        mock_db = MagicMock()
        mock_db.cloud_pool = mock_pool
        mock_gs.return_value.get_database.return_value = mock_db

        resp = client.get("/api/video/feed.xml")
        assert resp.status_code == 200
        sql = " ".join(captured)
        assert "media_approvals" in sql, "video feed must JOIN media_approvals"
        assert "'approved'" in sql, "video feed must require status='approved'"
        assert "'video'" in sql, "video feed must gate on medium='video'"
        assert "media_assets" in sql, "video feed must source from media_assets"
        # Mirror the podcast feed's niche-policy seam (feedback_filter_on_seams_not_slugs).
        assert "media_to_generate" in sql

    @patch("poindexter.utils.route_utils.get_services")
    def test_empty_feed_when_nothing_approved(self, mock_gs):
        """No approved rows → query returns [] → feed renders no items."""
        mock_conn = AsyncMock()
        mock_conn.fetch.return_value = []
        ctx = MagicMock()
        ctx.__aenter__ = AsyncMock(return_value=mock_conn)
        ctx.__aexit__ = AsyncMock(return_value=False)
        mock_pool = MagicMock()
        mock_pool.acquire.return_value = ctx
        mock_db = MagicMock()
        mock_db.cloud_pool = mock_pool
        mock_gs.return_value.get_database.return_value = mock_db

        resp = client.get("/api/video/feed.xml")
        assert resp.status_code == 200
        assert "<item>" not in resp.text


def _pool_serving(rows=None, captured=None):
    async def _fetch(sql, *_a, **_kw):
        if captured is not None:
            captured.append(sql)
        return rows or []

    mock_conn = AsyncMock()
    mock_conn.fetch = AsyncMock(side_effect=_fetch)
    ctx = MagicMock()
    ctx.__aenter__ = AsyncMock(return_value=mock_conn)
    ctx.__aexit__ = AsyncMock(return_value=False)
    mock_pool = MagicMock()
    mock_pool.acquire.return_value = ctx
    mock_db = MagicMock()
    mock_db.cloud_pool = mock_pool
    return mock_db


class TestVideoFeedEnclosureKey:
    """poindexter#1085: the feed advertised ``video/{post_id}.mp4`` for every
    unstamped row while nothing wrote that key after #1460."""

    @patch("poindexter.utils.route_utils.get_services")
    def test_unstamped_row_falls_back_to_the_key_the_mirror_writes(self, mock_gs):
        from poindexter.services.r2_upload_service import video_episode_key

        mock_gs.return_value.get_database.return_value = _pool_serving([{
            "post_id": "post-2",
            "title": "Not mirrored yet",
            "slug": "not-mirrored-yet",
            "excerpt": "",
            "published_at": datetime(2026, 9, 15, tzinfo=timezone.utc),
            "url": "",
            "file_size_bytes": 89_160_365,
        }])

        resp = client.get("/api/video/feed.xml")

        assert resp.status_code == 200
        expected = f"https://pub-test-bucket.r2.dev/{video_episode_key('post-2')}"
        assert f'url="{expected}"' in resp.text
        assert 'length="89160365"' in resp.text

    @patch("poindexter.utils.route_utils.get_services")
    def test_the_mirror_selects_with_the_feeds_own_gates(self, mock_gs):
        """Two independent spellings of one selection must agree: if the feed
        gains or drops a gate, the mirror has to follow, or it uploads videos
        nobody lists (or misses ones the feed advertises)."""
        from poindexter.services.video_r2_mirror import _CANDIDATES_SQL

        captured: list[str] = []
        mock_gs.return_value.get_database.return_value = _pool_serving(captured=captured)
        client.get("/api/video/feed.xml")
        feed_sql = " ".join(captured)

        for gate in (
            "status = 'published'",
            "'video' = ANY(",
            "media_to_generate",
            "medium = 'video'",
            "status = 'approved'",
            "type = 'video'",
            "DISTINCT ON (p.id)",
            "created_at DESC NULLS LAST",
        ):
            assert gate in feed_sql, f"feed lost gate {gate!r}"
            assert gate in _CANDIDATES_SQL, f"mirror lost gate {gate!r}"


class TestVideoFeedWithoutStoragePublicUrl:
    """poindexter#485: the feed never guesses a bucket. With an episode to list
    and ``storage_public_url`` unset it answers 503 naming the setting. An
    empty feed never reaches that lookup, so it still renders."""

    _unconfigured = SiteConfig(initial_config={
        "video_feed_name": "Test Video",
        "site_url": "https://www.test-site.example.com",
        "site_domain": "test-site.example.com",
    })

    def _client(self):
        app = FastAPI()
        app.include_router(router)
        from poindexter.utils.route_utils import get_site_config_dependency
        app.dependency_overrides[get_site_config_dependency] = lambda: self._unconfigured
        return TestClient(app, raise_server_exceptions=False)

    @patch("poindexter.utils.route_utils.get_services")
    def test_an_unstamped_episode_503s_naming_the_setting(self, mock_gs):
        mock_gs.return_value.get_database.return_value = _pool_serving([{
            "post_id": "post-3",
            "title": "Needs the fallback key",
            "slug": "needs-the-fallback-key",
            "excerpt": "",
            "published_at": datetime(2026, 9, 1, tzinfo=timezone.utc),
            "url": "",
            "file_size_bytes": 1,
        }])

        resp = self._client().get("/api/video/feed.xml")

        assert resp.status_code == 503
        assert "storage_public_url" in resp.json()["detail"]

    @patch("poindexter.utils.route_utils.get_services")
    def test_an_empty_feed_still_renders(self, mock_gs):
        mock_gs.return_value.get_database.return_value = _pool_serving([])

        resp = self._client().get("/api/video/feed.xml")

        assert resp.status_code == 200
        assert "<item>" not in resp.text
