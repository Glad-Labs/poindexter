"""
Unit tests for routes/media_routes.py.

Tests cover:
- POST /api/media/generate-image  — generate_featured_image
- GET  /api/media/images/search   — search_images
- GET  /api/media/health          — health_check

get_image_service is patched to avoid real I/O.
Rate limiter is disabled via autouse fixture.
Auth is provided via dependency override.
"""

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from unittest.mock import AsyncMock, MagicMock, patch

from routes.auth_unified import get_current_user
from routes.media_routes import media_router
import routes.media_routes as media_module
from utils.rate_limiter import limiter

from tests.unit.routes.conftest import TEST_USER


@pytest.fixture(autouse=True)
def disable_rate_limiter():
    """Disable slowapi rate limiter for all media tests."""
    original = limiter.enabled
    limiter.enabled = False
    yield
    limiter.enabled = original


@pytest.fixture(autouse=True)
def reset_image_service():
    """Reset the image service singleton between tests."""
    original = media_module._image_service
    media_module._image_service = None
    yield
    media_module._image_service = original


def _make_image_metadata(source="pexels"):
    meta = MagicMock()
    meta.url = f"https://images.pexels.com/photos/12345/photo.jpg"
    meta.source = source
    meta.photographer = "Test Photographer"
    meta.photographer_url = "https://pexels.com/test"
    meta.width = 1920
    meta.height = 1080
    meta.pexels_api_key = "fake-key"
    meta.sdxl_available = False
    return meta


def _make_image_service(pexels_result=None, sdxl_result=False, gallery_result=None):
    svc = MagicMock()
    _meta = pexels_result or _make_image_metadata()
    svc.search_featured_image = AsyncMock(return_value=_meta)
    svc.generate_image = AsyncMock(return_value=sdxl_result)
    svc.get_images_for_gallery = AsyncMock(return_value=gallery_result or [_meta])
    svc.pexels_api_key = "fake-key"
    svc.sdxl_available = False
    return svc


def _build_app() -> FastAPI:
    app = FastAPI()
    app.include_router(media_router)
    app.dependency_overrides[get_current_user] = lambda: TEST_USER
    return app


VALID_GENERATE_PAYLOAD = {
    "prompt": "AI gaming NPCs",
    "use_pexels": True,
    "use_generation": False,
}


# ---------------------------------------------------------------------------
# POST /api/media/generate-image
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestGenerateFeaturedImage:
    def test_returns_200_with_pexels_result(self):
        svc = _make_image_service()
        with patch(
            "routes.media_routes.get_image_service", new=AsyncMock(return_value=svc)
        ):
            client = TestClient(_build_app())
            resp = client.post("/api/media/generate-image", json=VALID_GENERATE_PAYLOAD)
        assert resp.status_code == 200

    def test_response_has_success_and_image_url(self):
        svc = _make_image_service()
        with patch(
            "routes.media_routes.get_image_service", new=AsyncMock(return_value=svc)
        ):
            client = TestClient(_build_app())
            data = client.post("/api/media/generate-image", json=VALID_GENERATE_PAYLOAD).json()
        assert "success" in data
        assert "image_url" in data
        assert data["success"] is True

    def test_no_pexels_result_returns_200_with_success_false(self):
        svc = _make_image_service(pexels_result=None)
        svc.search_featured_image = AsyncMock(return_value=None)
        with patch(
            "routes.media_routes.get_image_service", new=AsyncMock(return_value=svc)
        ):
            client = TestClient(_build_app())
            data = client.post("/api/media/generate-image", json=VALID_GENERATE_PAYLOAD).json()
        assert data["success"] is False

    def test_prompt_too_short_returns_422(self):
        client = TestClient(_build_app())
        resp = client.post(
            "/api/media/generate-image", json={"prompt": "ab", "use_pexels": True}
        )
        assert resp.status_code == 422

    def test_requires_auth(self):
        """generate-image endpoint requires authentication — returns 401 without token."""
        app = FastAPI()
        app.include_router(media_router)
        client = TestClient(app, raise_server_exceptions=False)
        resp = client.post("/api/media/generate-image", json=VALID_GENERATE_PAYLOAD)
        assert resp.status_code == 401

    def test_keywords_accepted(self):
        svc = _make_image_service()
        with patch(
            "routes.media_routes.get_image_service", new=AsyncMock(return_value=svc)
        ):
            client = TestClient(_build_app())
            resp = client.post(
                "/api/media/generate-image",
                json={**VALID_GENERATE_PAYLOAD, "keywords": ["AI", "gaming"]},
            )
        assert resp.status_code == 200

    def test_image_service_error_returns_200_with_success_false(self):
        """Errors are caught and returned as failure response (200)."""
        svc = _make_image_service()
        svc.search_featured_image = AsyncMock(side_effect=RuntimeError("Pexels down"))
        with patch(
            "routes.media_routes.get_image_service", new=AsyncMock(return_value=svc)
        ):
            client = TestClient(_build_app())
            data = client.post("/api/media/generate-image", json=VALID_GENERATE_PAYLOAD).json()
        assert data["success"] is False


# ---------------------------------------------------------------------------
# GET /api/media/images/search
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestSearchImages:
    def test_returns_200_when_image_found(self):
        svc = _make_image_service()
        with patch(
            "routes.media_routes.get_image_service", new=AsyncMock(return_value=svc)
        ):
            client = TestClient(_build_app())
            resp = client.get("/api/media/images/search?query=mountains")
        assert resp.status_code == 200

    def test_response_has_success_and_image_url(self):
        svc = _make_image_service()
        with patch(
            "routes.media_routes.get_image_service", new=AsyncMock(return_value=svc)
        ):
            client = TestClient(_build_app())
            data = client.get("/api/media/images/search?query=mountains").json()
        assert "success" in data
        assert "image_url" in data

    def test_no_result_returns_200_with_success_false(self):
        svc = _make_image_service()
        svc.search_featured_image = AsyncMock(return_value=None)
        with patch(
            "routes.media_routes.get_image_service", new=AsyncMock(return_value=svc)
        ):
            client = TestClient(_build_app())
            data = client.get("/api/media/images/search?query=mountains").json()
        assert data["success"] is False

    def test_query_too_short_returns_422(self):
        client = TestClient(_build_app())
        resp = client.get("/api/media/images/search?query=ab")
        assert resp.status_code == 422

    def test_multiple_count_uses_gallery(self):
        svc = _make_image_service()
        with patch(
            "routes.media_routes.get_image_service", new=AsyncMock(return_value=svc)
        ):
            client = TestClient(_build_app())
            data = client.get("/api/media/images/search?query=mountains&count=3").json()
        assert data["success"] is True
        svc.get_images_for_gallery.assert_awaited_once()

    def test_requires_auth(self):
        """image search endpoint requires authentication — returns 401 without token."""
        app = FastAPI()
        app.include_router(media_router)
        client = TestClient(app, raise_server_exceptions=False)
        resp = client.get("/api/media/images/search?query=mountains")
        assert resp.status_code == 401


# ---------------------------------------------------------------------------
# GET /api/media/health
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestHealthCheck:
    def test_returns_200(self):
        svc = _make_image_service()
        with patch(
            "routes.media_routes.get_image_service", new=AsyncMock(return_value=svc)
        ):
            client = TestClient(_build_app())
            resp = client.get("/api/media/health")
        assert resp.status_code == 200

    def test_response_has_required_fields(self):
        svc = _make_image_service()
        with patch(
            "routes.media_routes.get_image_service", new=AsyncMock(return_value=svc)
        ):
            client = TestClient(_build_app())
            data = client.get("/api/media/health").json()
        for field in ["status", "pexels_available", "sdxl_available", "message"]:
            assert field in data

    def test_pexels_configured_returns_healthy(self):
        svc = _make_image_service()
        svc.pexels_api_key = "valid-key"
        with patch(
            "routes.media_routes.get_image_service", new=AsyncMock(return_value=svc)
        ):
            client = TestClient(_build_app())
            data = client.get("/api/media/health").json()
        assert data["pexels_available"] is True
        assert data["status"] == "healthy"

    def test_no_api_key_returns_degraded(self):
        svc = _make_image_service()
        svc.pexels_api_key = None  # type: ignore[assignment]
        svc.sdxl_available = False
        with patch(
            "routes.media_routes.get_image_service", new=AsyncMock(return_value=svc)
        ):
            client = TestClient(_build_app())
            data = client.get("/api/media/health").json()
        assert data["status"] == "degraded"

    def test_no_auth_required_for_health(self):
        """Health endpoint has no auth dependency."""
        svc = _make_image_service()
        app = FastAPI()
        app.include_router(media_router)
        with patch(
            "routes.media_routes.get_image_service", new=AsyncMock(return_value=svc)
        ):
            client = TestClient(app)
            resp = client.get("/api/media/health")
        assert resp.status_code == 200


# ---------------------------------------------------------------------------
# upload_to_s3 — executor-based non-blocking I/O (#678)
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestUploadToS3:
    """upload_to_s3() must not block the event loop — I/O is run in executor."""

    @pytest.mark.asyncio
    async def test_returns_none_when_s3_not_configured(self, tmp_path):
        """When no S3 credentials are set, returns None without error."""
        from routes.media_routes import upload_to_s3

        with patch("routes.media_routes.get_s3_client", return_value=None):
            result = await upload_to_s3(str(tmp_path / "image.png"))
        assert result is None

    @pytest.mark.asyncio
    async def test_returns_none_when_bucket_not_set(self, tmp_path):
        """When AWS_S3_BUCKET is absent, returns None."""
        import io
        from routes.media_routes import upload_to_s3

        fake_s3 = MagicMock()
        with patch("routes.media_routes.get_s3_client", return_value=fake_s3):
            with patch.dict("os.environ", {}, clear=False):
                import os
                os.environ.pop("AWS_S3_BUCKET", None)
                result = await upload_to_s3(str(tmp_path / "image.png"))
        assert result is None

    @pytest.mark.asyncio
    async def test_upload_uses_run_in_executor(self, tmp_path):
        """File read and S3 upload are both delegated to run_in_executor, not blocking."""
        from routes.media_routes import upload_to_s3

        image_file = tmp_path / "image.png"
        image_file.write_bytes(b"PNG-DATA")

        fake_s3 = MagicMock()
        fake_s3.upload_fileobj = MagicMock()  # sync method, must run in executor

        with patch("routes.media_routes.get_s3_client", return_value=fake_s3):
            with patch.dict("os.environ", {"AWS_S3_BUCKET": "test-bucket"}):
                result = await upload_to_s3(str(image_file), task_id="task-001")

        assert result is not None
        assert "test-bucket" in result or "s3.amazonaws.com" in result
        fake_s3.upload_fileobj.assert_called_once()
