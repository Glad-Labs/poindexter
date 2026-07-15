"""Unit tests for ``routes/media_approval_routes.py``.

Covers the ``GET /api/media-approval/{post_id}/{medium}/preview`` route: the
operator console's only way to see/hear a Gate-2 item before deciding on it
(the asset lives only on local disk until approved — see the
``media_approval_service`` module docstring). ``pending`` / ``decide`` are
covered by ``tests/unit/services/test_media_approval_service.py`` (the
service layer) + ``test_operator_routers_require_auth.py`` (structural auth);
this file is the HTTP-layer contract for the streaming route, mirroring
``test_video_routes.py::TestStreamVideo``.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from middleware.api_token_auth import verify_api_token
from routes.media_approval_routes import router
from utils.route_utils import get_database_dependency

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
            "services.media_approval_service.get_asset_storage_path",
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
            "services.media_approval_service.get_asset_storage_path",
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
            "services.media_approval_service.get_asset_storage_path",
            new=AsyncMock(return_value=str(video_file)),
        ):
            resp = TestClient(_build_app()).get(
                "/api/media-approval/post-1/video_short/preview"
            )

        assert resp.status_code == 200
        assert resp.headers["content-type"] == "video/mp4"

    def test_404_when_no_asset_row(self):
        with patch(
            "services.media_approval_service.get_asset_storage_path",
            new=AsyncMock(return_value=None),
        ):
            resp = TestClient(_build_app()).get(
                "/api/media-approval/post-1/video/preview"
            )

        assert resp.status_code == 404

    def test_404_when_file_missing_on_disk(self):
        with patch(
            "services.media_approval_service.get_asset_storage_path",
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
            "services.media_approval_service.get_asset_storage_path",
            new=AsyncMock(return_value=str(video_file)),
        ):
            resp = TestClient(_build_app(authed=False)).get(
                "/api/media-approval/post-1/video/preview"
            )

        assert resp.status_code == 401
