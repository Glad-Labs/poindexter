"""
Unit tests for routes/model_routes.py.

Tests cover:
- GET /api/models/available    — get_available_models
- GET /api/models/status       — get_provider_status
- GET /api/models/recommended  — get_recommended_models
- GET /api/models/rtx5070      — deprecated redirect
- GET /api/models              — get_models_list (legacy)

Auth is router-level (dependencies=[Depends(get_current_user)]) — override on the
FastAPI app. ModelConsolidationService is patched to avoid real provider calls.
"""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from routes.auth_unified import get_current_user
from routes.model_routes import models_router
from tests.unit.routes.conftest import TEST_USER


def _make_service(models=None, status=None):
    svc = MagicMock()
    svc.list_models = AsyncMock(
        return_value=models
        or {
            "ollama": ["llama3.2:3b", "mistral:7b"],
            "anthropic": ["claude-3-haiku-20240307"],
            "openai": ["gpt-4o-mini"],
        }
    )
    svc.get_status = MagicMock(
        return_value=status
        or {
            "ollama": {"available": True, "model_count": 2},
            "anthropic": {"available": True, "model_count": 1},
        }
    )
    return svc


def _build_app(service=None) -> FastAPI:
    app = FastAPI()
    app.include_router(models_router)
    app.dependency_overrides[get_current_user] = lambda: TEST_USER
    return app


# ---------------------------------------------------------------------------
# GET /api/models/available
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestGetAvailableModels:
    def test_returns_200(self):
        with patch(
            "routes.model_routes.get_model_consolidation_service",
            return_value=_make_service(),
        ):
            client = TestClient(_build_app())
            resp = client.get("/api/models/available")
        assert resp.status_code == 200

    def test_response_has_models_and_total(self):
        with patch(
            "routes.model_routes.get_model_consolidation_service",
            return_value=_make_service(),
        ):
            client = TestClient(_build_app())
            data = client.get("/api/models/available").json()
        assert "models" in data
        assert "total" in data
        assert data["total"] == len(data["models"])

    def test_response_includes_timestamp(self):
        with patch(
            "routes.model_routes.get_model_consolidation_service",
            return_value=_make_service(),
        ):
            client = TestClient(_build_app())
            data = client.get("/api/models/available").json()
        assert "timestamp" in data

    def test_each_model_has_required_fields(self):
        with patch(
            "routes.model_routes.get_model_consolidation_service",
            return_value=_make_service(),
        ):
            client = TestClient(_build_app())
            data = client.get("/api/models/available").json()
        if data["models"]:
            model = data["models"][0]
            for field in ["name", "provider", "isFree"]:
                assert field in model, f"Missing field: {field}"

    def test_vram_filter_returns_200(self):
        with patch(
            "routes.model_routes.get_model_consolidation_service",
            return_value=_make_service(),
        ):
            client = TestClient(_build_app())
            resp = client.get("/api/models/available?vram_gb=8")
        assert resp.status_code == 200

    def test_vram_filter_limits_ollama_models(self):
        svc = _make_service(
            models={
                "ollama": ["m1", "m2", "m3", "m4", "m5"],
                "anthropic": [],
                "openai": [],
            }
        )
        with patch("routes.model_routes.get_model_consolidation_service", return_value=svc):
            client = TestClient(_build_app())
            data = client.get("/api/models/available?vram_gb=8").json()
        ollama_models = [m for m in data["models"] if m["provider"] == "ollama"]
        assert len(ollama_models) <= 2  # vram_gb=8 <= 12 → limit 2

    def test_service_error_returns_500(self):
        with patch(
            "routes.model_routes.get_model_consolidation_service",
            side_effect=RuntimeError("service down"),
        ):
            client = TestClient(_build_app())
            resp = client.get("/api/models/available")
        assert resp.status_code == 500

    def test_requires_auth(self):
        app = FastAPI()
        app.include_router(models_router)
        client = TestClient(app, raise_server_exceptions=False)
        resp = client.get("/api/models/available")
        assert resp.status_code == 401


# ---------------------------------------------------------------------------
# GET /api/models/status
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestGetProviderStatus:
    def test_returns_200(self):
        with patch(
            "routes.model_routes.get_model_consolidation_service",
            return_value=_make_service(),
        ):
            client = TestClient(_build_app())
            resp = client.get("/api/models/status")
        assert resp.status_code == 200

    def test_response_has_providers_and_timestamp(self):
        with patch(
            "routes.model_routes.get_model_consolidation_service",
            return_value=_make_service(),
        ):
            client = TestClient(_build_app())
            data = client.get("/api/models/status").json()
        assert "providers" in data
        assert "timestamp" in data

    def test_service_error_returns_500(self):
        with patch(
            "routes.model_routes.get_model_consolidation_service",
            side_effect=RuntimeError("provider check failed"),
        ):
            client = TestClient(_build_app())
            resp = client.get("/api/models/status")
        assert resp.status_code == 500


# ---------------------------------------------------------------------------
# GET /api/models/recommended
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestGetRecommendedModels:
    def test_returns_200(self):
        with patch(
            "routes.model_routes.get_model_consolidation_service",
            return_value=_make_service(),
        ):
            client = TestClient(_build_app())
            resp = client.get("/api/models/recommended")
        assert resp.status_code == 200

    def test_returns_one_model_per_provider(self):
        svc = _make_service(
            models={
                "ollama": ["llama3.2:3b", "mistral:7b"],
                "anthropic": ["claude-3-haiku-20240307", "claude-3-sonnet-20240229"],
            }
        )
        with patch("routes.model_routes.get_model_consolidation_service", return_value=svc):
            client = TestClient(_build_app())
            data = client.get("/api/models/recommended").json()
        # At most one model per provider in the response
        providers = [m["provider"] for m in data["models"]]
        assert len(providers) == len(set(providers)), "Duplicate providers in recommended"

    def test_service_error_returns_500(self):
        with patch(
            "routes.model_routes.get_model_consolidation_service",
            side_effect=RuntimeError("service down"),
        ):
            client = TestClient(_build_app())
            resp = client.get("/api/models/recommended")
        assert resp.status_code == 500


# ---------------------------------------------------------------------------
# GET /api/models/rtx5070 (deprecated redirect)
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestRtx5070Redirect:
    def test_returns_301_redirect(self):
        client = TestClient(_build_app(), follow_redirects=False)
        resp = client.get("/api/models/rtx5070")
        assert resp.status_code == 301

    def test_redirect_location_points_to_available(self):
        client = TestClient(_build_app(), follow_redirects=False)
        resp = client.get("/api/models/rtx5070")
        location = resp.headers.get("location", "")
        assert "/api/models/available" in location
        assert "vram_gb=12" in location


# ---------------------------------------------------------------------------
# GET /api/models (legacy endpoint)
# ---------------------------------------------------------------------------
