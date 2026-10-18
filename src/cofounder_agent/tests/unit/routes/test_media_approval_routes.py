"""Unit tests for ``routes/media_approval_routes.py``.

Covers the ``GET /api/media-approval/{post_id}/{medium}/preview`` route: the
operator console's only way to see/hear a Gate-2 item before deciding on it
(the asset lives only on local disk until approved — see the
``media_approval_service`` module docstring). ``pending`` / ``decide`` are
covered by ``tests/unit/services/test_media_approval_service.py`` (the
service layer) + ``test_operator_routers_require_auth.py`` (structural auth);
this file is the HTTP-layer contract for the streaming route. Since
Glad-Labs/poindexter#1087 removed ``/api/video/episodes/{post_id}.mp4``, it is
the only route that streams a video render off local disk.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from middleware.api_token_auth import verify_api_token
from poindexter.routes.media_approval_routes import router
from poindexter.utils.route_utils import get_database_dependency

pytestmark = pytest.mark.unit


def _build_app(*, authed: bool = True) -> FastAPI:
    app = FastAPI()
    app.include_router(router)
    db = MagicMock()
    db.pool = MagicMock(name="pool")
    app.dependency_overrides[get_database_dependency] = lambda: db
    if authed:
        app.dependency_overrides[verify_api_token] = lambda: "test-token"
    return app


class TestPreview:
    def test_streams_video_with_correct_mime(self, tmp_path):
        video_file = tmp_path / "task-abc.mp4"
        video_file.write_bytes(b"\x00\x00\x00\x18ftypmp42" * 10)

        with patch(
            "poindexter.services.media_approval_service.get_asset_storage_path",
            new=AsyncMock(return_value=str(video_file)),
        ):
            resp = TestClient(_build_app()).get(
                "/api/media-approval/post-1/video/preview"
            )

        assert resp.status_code == 200
        assert resp.headers["content-type"] == "video/mp4"
        assert resp.content == video_file.read_bytes()

    def test_streams_podcast_with_correct_mime(self, tmp_path):
        audio_file = tmp_path / "task-abc.mp3"
        audio_file.write_bytes(b"ID3" + b"\x00" * 50)

        with patch(
            "poindexter.services.media_approval_service.get_asset_storage_path",
            new=AsyncMock(return_value=str(audio_file)),
        ):
            resp = TestClient(_build_app()).get(
                "/api/media-approval/post-1/podcast/preview"
            )

        assert resp.status_code == 200
        assert resp.headers["content-type"] == "audio/mpeg"

    def test_video_short_uses_video_mime(self, tmp_path):
        video_file = tmp_path / "task-abc-short.mp4"
        video_file.write_bytes(b"\x00" * 20)

        with patch(
            "poindexter.services.media_approval_service.get_asset_storage_path",
            new=AsyncMock(return_value=str(video_file)),
        ):
            resp = TestClient(_build_app()).get(
                "/api/media-approval/post-1/video_short/preview"
            )

        assert resp.status_code == 200
        assert resp.headers["content-type"] == "video/mp4"

    def test_404_when_no_asset_row(self):
        with patch(
            "poindexter.services.media_approval_service.get_asset_storage_path",
            new=AsyncMock(return_value=None),
        ):
            resp = TestClient(_build_app()).get(
                "/api/media-approval/post-1/video/preview"
            )

        assert resp.status_code == 404

    def test_404_when_file_missing_on_disk(self):
        with patch(
            "poindexter.services.media_approval_service.get_asset_storage_path",
            new=AsyncMock(return_value="/nonexistent/path/ghost.mp4"),
        ):
            resp = TestClient(_build_app()).get(
                "/api/media-approval/post-1/video/preview"
            )

        assert resp.status_code == 404

    def test_requires_auth(self, tmp_path):
        video_file = tmp_path / "task-abc.mp4"
        video_file.write_bytes(b"\x00" * 20)

        with patch(
            "poindexter.services.media_approval_service.get_asset_storage_path",
            new=AsyncMock(return_value=str(video_file)),
        ):
            resp = TestClient(_build_app(authed=False)).get(
                "/api/media-approval/post-1/video/preview"
            )

        assert resp.status_code == 401


class TestThumbnail:
    """The composed YouTube thumbnail is public the moment the video is, so
    the drawer shows it beside the video it uploads with."""

    def test_streams_the_thumbnail_as_jpeg(self, tmp_path):
        thumb = tmp_path / "t_thumbnail.jpg"
        thumb.write_bytes(b"\xff\xd8\xff\xe0jpeg")
        lookup = AsyncMock(return_value=str(thumb))
        with patch("poindexter.services.media_approval_service.get_thumbnail_storage_path", new=lookup):
            resp = TestClient(_build_app()).get("/api/media-approval/post-1/video/thumbnail")
        assert resp.status_code == 200
        assert resp.headers["content-type"] == "image/jpeg"
        assert resp.headers["cache-control"] == "no-store"
        assert resp.content == thumb.read_bytes()
        assert lookup.await_args.args[1] == "post-1"

    def test_the_short_streams_its_own_vertical_thumbnail(self, tmp_path):
        thumb = tmp_path / "t_short_thumbnail.jpg"
        thumb.write_bytes(b"\xff\xd8\xff\xe0jpeg")
        lookup = AsyncMock(return_value=str(thumb))
        with patch("poindexter.services.media_approval_service.get_thumbnail_storage_path", new=lookup):
            resp = TestClient(_build_app()).get("/api/media-approval/post-1/video_short/thumbnail")
        assert resp.status_code == 200 and resp.content == thumb.read_bytes()
        assert lookup.await_args.args[1:] == ("post-1", "video_short")

    def test_a_podcast_has_none(self):
        resp = TestClient(_build_app()).get("/api/media-approval/post-1/podcast/thumbnail")
        assert resp.status_code == 404

    def test_no_thumbnail_on_disk_is_a_404(self, tmp_path):
        with patch(
            "poindexter.services.media_approval_service.get_thumbnail_storage_path",
            new=AsyncMock(return_value=str(tmp_path / "gone.jpg")),
        ):
            resp = TestClient(_build_app()).get("/api/media-approval/post-1/video/thumbnail")
        assert resp.status_code == 404

    def test_requires_auth(self):
        resp = TestClient(_build_app(authed=False)).get("/api/media-approval/post-1/video/thumbnail")
        assert resp.status_code in (401, 403)


class TestRejectReasons:
    """A media reject says why (2026-10-18): the console's reason chips come
    from GET /reject-reasons, and decide() takes the chosen code."""

    @staticmethod
    def _app(raw: str = "voice=Voice or narration; hook=Weak opening") -> FastAPI:
        from poindexter.services.site_config import SiteConfig
        from poindexter.utils.route_utils import get_site_config_dependency

        app = _build_app()
        sc = SiteConfig(initial_config={"media_reject_reasons": raw})
        app.dependency_overrides[get_site_config_dependency] = lambda: sc
        return app

    def test_lists_the_configured_reasons(self):
        resp = TestClient(self._app()).get("/api/media-approval/reject-reasons")
        assert resp.status_code == 200
        assert resp.json() == {"reasons": [
            {"code": "voice", "label": "Voice or narration"},
            {"code": "hook", "label": "Weak opening"},
        ]}

    def test_decide_passes_the_reason_through(self):
        decide = AsyncMock()
        with patch("poindexter.services.media_approval_service.decide", new=decide), \
             patch("poindexter.services.media_feed_rebuild.rebuild_feed_for_medium", new=AsyncMock()):
            resp = TestClient(self._app()).post(
                "/api/media-approval/post-1/video_short/decide",
                json={"approved": False, "notes": "cracks", "reason": "Voice"},
            )
        assert resp.status_code == 200, resp.text
        assert decide.await_args.kwargs["reason"] == "Voice"
        assert decide.await_args.kwargs["notes"] == "cracks"
        assert resp.json()["reason"] == "voice"

    def test_an_unknown_reason_is_a_422_not_a_404(self):
        """404 means 'no such row'; a bad reason is the caller's input."""
        db_row = AsyncMock(return_value={"status": "rejected"})
        app = self._app()
        from poindexter.utils.route_utils import get_database_dependency

        db = MagicMock()
        db.pool.fetchrow = db_row
        app.dependency_overrides[get_database_dependency] = lambda: db
        resp = TestClient(app).post(
            "/api/media-approval/post-1/video/decide",
            json={"approved": False, "reason": "boring"},
        )
        assert resp.status_code == 422
        assert "boring" in resp.json()["detail"]
        db_row.assert_not_called()

    def test_requires_auth(self):
        resp = TestClient(_build_app(authed=False)).get("/api/media-approval/reject-reasons")
        assert resp.status_code in (401, 403)
