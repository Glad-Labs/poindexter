"""
Podcast Routes — Unit Tests

Tests for RSS feed generation, episode streaming, listing, and manual generation.
"""

from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

from fastapi import FastAPI
from fastapi.testclient import TestClient

from routes.podcast_routes import (
    _build_rss_xml,
    _format_duration,
    _rfc2822,
    router,
)
from services.site_config import site_config as _test_site_config

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


# ---------------------------------------------------------------------------
# GET /api/podcast/feed.xml
# ---------------------------------------------------------------------------


class TestPodcastFeed:
    @patch("routes.podcast_routes.PodcastService")
    @patch("utils.route_utils.get_services")
    def test_empty_feed_when_no_episodes(self, mock_get_services, mock_svc_cls):
        # Mock the lazy import
        with patch("routes.podcast_routes.get_services", create=True) as mock_gs:
            mock_db = MagicMock()
            mock_db.pool = None
            mock_db.cloud_pool = None
            mock_gs.return_value.get_database.return_value = mock_db

            mock_svc = MagicMock()
            mock_svc.list_episodes.return_value = []
            mock_svc_cls.return_value = mock_svc

            resp = client.get("/api/podcast/feed.xml")
            assert resp.status_code == 200
            assert "application/rss+xml" in resp.headers["content-type"]
            assert "<item>" not in resp.text


# ---------------------------------------------------------------------------
# GET /api/podcast/episodes/{post_id}.mp3
# ---------------------------------------------------------------------------


class TestStreamEpisode:
    def test_missing_episode_returns_404(self):
        with patch("routes.podcast_routes.PODCAST_DIR", Path("/nonexistent/path")):
            resp = client.get("/api/podcast/episodes/abc123.mp3")
            assert resp.status_code == 404

    def test_path_traversal_blocked(self):
        with patch("routes.podcast_routes.PODCAST_DIR", Path("/tmp/podcasts")):
            resp = client.get("/api/podcast/episodes/..%2F..%2Fetc%2Fpasswd.mp3")
            assert resp.status_code == 404

    def test_valid_episode_served(self, tmp_path):
        mp3_file = tmp_path / "test123.mp3"
        mp3_file.write_bytes(b"\xff\xfb\x90\x00" * 100)

        with patch("routes.podcast_routes.PODCAST_DIR", tmp_path):
            resp = client.get("/api/podcast/episodes/test123.mp3")
            assert resp.status_code == 200
            assert resp.headers["content-type"] == "audio/mpeg"


# ---------------------------------------------------------------------------
# GET /api/podcast/episodes
# ---------------------------------------------------------------------------


class TestListEpisodes:
    @patch("routes.podcast_routes.PodcastService")
    def test_returns_json_list(self, mock_svc_cls):
        mock_svc = MagicMock()
        mock_svc.list_episodes.return_value = [
            {"post_id": "1", "file_path": "/tmp/1.mp3", "file_size_bytes": 5000},
            {"post_id": "2", "file_path": "/tmp/2.mp3", "file_size_bytes": 3000},
        ]
        mock_svc_cls.return_value = mock_svc

        resp = client.get("/api/podcast/episodes")
        assert resp.status_code == 200
        data = resp.json()
        assert data["count"] == 2
        assert len(data["episodes"]) == 2

    @patch("routes.podcast_routes.PodcastService")
    def test_empty_list(self, mock_svc_cls):
        mock_svc = MagicMock()
        mock_svc.list_episodes.return_value = []
        mock_svc_cls.return_value = mock_svc

        resp = client.get("/api/podcast/episodes")
        data = resp.json()
        assert data["count"] == 0
        assert data["episodes"] == []


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

    @patch("utils.route_utils.get_services")
    def test_no_db_returns_503(self, mock_gs):
        tc = self._make_app_with_auth_override()
        mock_db = MagicMock()
        mock_db.pool = None
        mock_db.cloud_pool = None
        mock_gs.return_value.get_database.return_value = mock_db

        resp = tc.post("/api/podcast/generate/abc123")
        assert resp.status_code == 503

    @patch("utils.route_utils.get_services")
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
        mock_db.cloud_pool = mock_pool
        mock_gs.return_value.get_database.return_value = mock_db

        resp = tc.post("/api/podcast/generate/nonexistent")
        assert resp.status_code == 404

    @patch("routes.podcast_routes.PodcastService")
    @patch("utils.route_utils.get_services")
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
        mock_db.cloud_pool = mock_pool
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

    @patch("routes.podcast_routes.PodcastService")
    @patch("utils.route_utils.get_services")
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
        mock_db.cloud_pool = mock_pool
        mock_gs.return_value.get_database.return_value = mock_db

        mock_result = MagicMock()
        mock_result.success = False
        mock_result.error = "TTS engine unavailable"

        mock_svc = MagicMock()
        mock_svc.generate_episode = AsyncMock(return_value=mock_result)
        mock_svc_cls.return_value = mock_svc

        resp = tc.post("/api/podcast/generate/post-1")
        assert resp.status_code == 500
