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

# ---------------------------------------------------------------------------
# Test app
# ---------------------------------------------------------------------------


def _build_app():
    app = FastAPI()
    app.include_router(router)
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
            assert data["count"] == 2
            assert len(data["episodes"]) == 2

    def test_empty_when_no_videos(self, tmp_path):
        with patch("routes.video_routes.VIDEO_DIR", tmp_path):
            resp = client.get("/api/video/episodes")
            data = resp.json()
            assert data["count"] == 0
            assert data["episodes"] == []

    def test_empty_when_dir_missing(self):
        with patch("routes.video_routes.VIDEO_DIR", Path("/nonexistent/dir")):
            resp = client.get("/api/video/episodes")
            data = resp.json()
            assert data["count"] == 0


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
    def test_feed_with_episodes(self, mock_gs, tmp_path):
        # Create video file
        (tmp_path / "post-1.mp4").write_bytes(b"\x00" * 5000)

        mock_conn = AsyncMock()
        mock_conn.fetch.return_value = [
            {
                "id": "post-1",
                "title": "Test Video",
                "slug": "test-video",
                "excerpt": "A test video",
                "published_at": datetime(2026, 4, 1, 12, 0, 0, tzinfo=timezone.utc),
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

        with patch("routes.video_routes.VIDEO_DIR", tmp_path):
            resp = client.get("/api/video/feed.xml")
            assert resp.status_code == 200
            assert "<item>" in resp.text
            assert "Test Video" in resp.text
            assert "video/mp4" in resp.text


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
