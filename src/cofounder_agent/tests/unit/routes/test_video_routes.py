"""
Video Routes — Unit Tests

Tests for video RSS feed, episode streaming, listing, and manual generation.
"""

from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

from fastapi import FastAPI
from fastapi.testclient import TestClient

from routes.video_routes import _rfc2822, router
from services.site_config import SiteConfig

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
    from utils.route_utils import get_site_config_dependency
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
# GET /api/video/episodes/{post_id}.mp4
# ---------------------------------------------------------------------------


class TestStreamVideo:
    def test_missing_video_returns_404(self):
        with patch("routes.video_routes.VIDEO_DIR", Path("/nonexistent/path")):
            resp = client.get("/api/video/episodes/abc123.mp4")
            assert resp.status_code == 404

    def test_path_traversal_blocked(self):
        with patch("routes.video_routes.VIDEO_DIR", Path("/tmp/videos")):
            resp = client.get("/api/video/episodes/..%2F..%2Fetc%2Fpasswd.mp4")
            assert resp.status_code == 404

    def test_valid_video_served(self, tmp_path):
        mp4_file = tmp_path / "test123.mp4"
        mp4_file.write_bytes(b"\x00\x00\x00\x18ftypmp42" * 10)

        with patch("routes.video_routes.VIDEO_DIR", tmp_path):
            resp = client.get("/api/video/episodes/test123.mp4")
            assert resp.status_code == 200
            assert resp.headers["content-type"] == "video/mp4"


# ---------------------------------------------------------------------------
# GET /api/video/episodes
# ---------------------------------------------------------------------------


class TestListVideoEpisodes:
    def test_returns_json_list(self, tmp_path):
        # Create some fake video files
        (tmp_path / "post1.mp4").write_bytes(b"\x00" * 1000)
        (tmp_path / "post2.mp4").write_bytes(b"\x00" * 2000)

        with patch("routes.video_routes.VIDEO_DIR", tmp_path):
            resp = client.get("/api/video/episodes")
            assert resp.status_code == 200
            data = resp.json()
            # Canonical offset envelope (poindexter#745): items, not the legacy
            # episodes/count keys. With pagination (#746) a no-param request
            # echoes the DEFAULT limit (50) — NOT len(items) as the pre-#746
            # unpaginated handler did.
            assert data["total"] == 2
            assert data["limit"] == 50
            assert data["offset"] == 0
            assert "episodes" not in data
            assert "count" not in data
            assert len(data["items"]) == 2
            # #636: the video listing never exposes an on-disk file_path.
            assert "file_path" not in data["items"][0]

    def test_empty_when_no_videos(self, tmp_path):
        with patch("routes.video_routes.VIDEO_DIR", tmp_path):
            resp = client.get("/api/video/episodes")
            data = resp.json()
            # Empty page still echoes the default limit (#746), not 0.
            assert data == {"items": [], "total": 0, "limit": 50, "offset": 0}

    def test_empty_when_dir_missing(self):
        with patch("routes.video_routes.VIDEO_DIR", Path("/nonexistent/dir")):
            resp = client.get("/api/video/episodes")
            data = resp.json()
            assert data == {"items": [], "total": 0, "limit": 50, "offset": 0}

    # --- pagination (#746 — apply the podcast fix to video) ------------------

    def test_pagination_limits_page(self, tmp_path):
        """?limit=2&offset=0 over 3 episodes returns the first 2, but `total`
        reports the FULL unpaginated count (3) — the linear-growth fix."""
        for i in range(1, 4):
            (tmp_path / f"post{i}.mp4").write_bytes(b"\x00" * (1000 * i))

        with patch("routes.video_routes.VIDEO_DIR", tmp_path):
            resp = client.get("/api/video/episodes?limit=2&offset=0")
            assert resp.status_code == 200
            data = resp.json()
            assert data["total"] == 3
            assert data["limit"] == 2
            assert data["offset"] == 0
            assert len(data["items"]) == 2

    def test_pagination_offset_skips(self, tmp_path):
        """?offset=2 skips the first 2 of 3 episodes; only the last remains.
        Episodes sort by filename, so offset 2 yields post3."""
        for i in range(1, 4):
            (tmp_path / f"post{i}.mp4").write_bytes(b"\x00" * (1000 * i))

        with patch("routes.video_routes.VIDEO_DIR", tmp_path):
            resp = client.get("/api/video/episodes?limit=2&offset=2")
            assert resp.status_code == 200
            data = resp.json()
            assert data["total"] == 3
            assert data["limit"] == 2
            assert data["offset"] == 2
            assert len(data["items"]) == 1
            assert data["items"][0]["post_id"] == "post3"

    def test_pagination_offset_past_end_is_empty(self, tmp_path):
        """An offset beyond the end returns an empty page (graceful Python
        slice, mirroring podcast), NOT a 404 — `total` still reports 3."""
        for i in range(1, 4):
            (tmp_path / f"post{i}.mp4").write_bytes(b"\x00" * 1000)

        with patch("routes.video_routes.VIDEO_DIR", tmp_path):
            resp = client.get("/api/video/episodes?offset=10")
            assert resp.status_code == 200
            data = resp.json()
            assert data["total"] == 3
            assert data["items"] == []

    def test_pagination_rejects_out_of_range_params(self, tmp_path):
        """Query bounds mirror the podcast endpoint exactly: limit in [1, 200],
        offset >= 0. Out-of-range values are rejected with 422 by FastAPI."""
        with patch("routes.video_routes.VIDEO_DIR", tmp_path):
            assert client.get("/api/video/episodes?limit=0").status_code == 422
            assert client.get("/api/video/episodes?limit=201").status_code == 422
            assert client.get("/api/video/episodes?offset=-1").status_code == 422


# ---------------------------------------------------------------------------
# GET /api/video/feed.xml
# ---------------------------------------------------------------------------


class TestVideoFeed:
    @patch("utils.route_utils.get_services")
    def test_empty_feed_when_no_videos(self, mock_gs, tmp_path):
        mock_db = MagicMock()
        mock_db.pool = None
        mock_db.cloud_pool = None
        mock_gs.return_value.get_database.return_value = mock_db

        with patch("routes.video_routes.VIDEO_DIR", tmp_path):
            resp = client.get("/api/video/feed.xml")
            assert resp.status_code == 200
            assert "application/rss+xml" in resp.headers["content-type"]
            assert "<item>" not in resp.text
            assert "Test Video" in resp.text

    @patch("utils.route_utils.get_services")
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

    @patch("utils.route_utils.get_services")
    def test_feed_query_requires_approved_media_approval(self, mock_gs):
        """The video feed MUST gate on an approved media_approvals row
        (medium='video') joined to a video media_assets row — mirroring the
        podcast feed and the operator requirement that ALL media is gated
        before any public surface (``feedback_approval_gate_all_media``).

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

    @patch("utils.route_utils.get_services")
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


# ---------------------------------------------------------------------------
# POST /api/video/generate/{post_id}
# ---------------------------------------------------------------------------


class TestGenerateVideo:
    def _make_client_with_auth_override(self):
        from middleware.api_token_auth import verify_api_token

        test_app = FastAPI()
        test_app.include_router(router)
        test_app.dependency_overrides[verify_api_token] = lambda: None
        return TestClient(test_app, raise_server_exceptions=False)

    @patch("utils.route_utils.get_services")
    def test_no_db_returns_503(self, mock_gs):
        tc = self._make_client_with_auth_override()
        mock_db = MagicMock()
        mock_db.pool = None
        mock_db.cloud_pool = None
        mock_gs.return_value.get_database.return_value = mock_db

        resp = tc.post("/api/video/generate/abc123")
        assert resp.status_code == 503

    @patch("utils.route_utils.get_services")
    def test_post_not_found_returns_404(self, mock_gs):
        tc = self._make_client_with_auth_override()
        mock_conn = AsyncMock()
        mock_conn.fetchrow.return_value = None

        ctx = MagicMock()
        ctx.__aenter__ = AsyncMock(return_value=mock_conn)
        ctx.__aexit__ = AsyncMock(return_value=False)
        mock_pool = MagicMock()
        mock_pool.acquire.return_value = ctx

        mock_db = MagicMock()
        mock_db.cloud_pool = mock_pool
        mock_gs.return_value.get_database.return_value = mock_db

        resp = tc.post("/api/video/generate/nonexistent")
        assert resp.status_code == 404

    @patch("services.video_service.generate_video_for_post")
    @patch("utils.route_utils.get_services")
    def test_successful_generation(self, mock_gs, mock_gen):
        tc = self._make_client_with_auth_override()

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
        mock_db.cloud_pool = mock_pool
        mock_gs.return_value.get_database.return_value = mock_db

        mock_result = MagicMock()
        mock_result.success = True
        mock_result.file_path = "/tmp/post-1.mp4"
        mock_result.duration_seconds = 30
        mock_result.file_size_bytes = 100000
        mock_result.images_used = 8
        mock_gen.return_value = mock_result

        resp = tc.post("/api/video/generate/post-1")
        assert resp.status_code == 200
        data = resp.json()
        assert data["success"] is True
        assert data["post_id"] == "post-1"

    @patch("services.video_service.generate_video_for_post")
    @patch("utils.route_utils.get_services")
    def test_generation_failure_returns_500(self, mock_gs, mock_gen):
        tc = self._make_client_with_auth_override()

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
        mock_db.cloud_pool = mock_pool
        mock_gs.return_value.get_database.return_value = mock_db

        mock_result = MagicMock()
        mock_result.success = False
        mock_result.error = "FFmpeg not available"
        mock_gen.return_value = mock_result

        resp = tc.post("/api/video/generate/post-1")
        assert resp.status_code == 500
