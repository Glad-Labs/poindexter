"""
Unit tests for routes/ollama_routes.py.

Tests cover:
- GET  /api/ollama/health        — check_ollama_health
- GET  /api/ollama/models        — get_ollama_models
- POST /api/ollama/warmup        — warmup_ollama
- GET  /api/ollama/status        — get_ollama_status
- POST /api/ollama/select-model  — select_ollama_model

All httpx calls are patched to avoid real network I/O.
Auth is router-level — overridden via dependency override.
"""

from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

import routes.ollama_routes as ollama_module
from middleware.api_token_auth import verify_api_token
from routes.ollama_routes import router
from tests.unit.routes.conftest import TEST_USER


@pytest.fixture(autouse=True)
def disable_warmup_limiter():
    """Disable the warmup rate limiter so test runs don't exhaust 5/minute."""
    original = ollama_module._warmup_limiter.enabled
    ollama_module._warmup_limiter.enabled = False
    yield
    ollama_module._warmup_limiter.enabled = original


def _build_app() -> FastAPI:
    app = FastAPI()
    app.include_router(router)
    app.dependency_overrides[verify_api_token] = lambda: "test-token"
    return app


def _make_httpx_response(status_code=200, json_data=None):
    resp = MagicMock(spec=httpx.Response)
    resp.status_code = status_code
    resp.json = MagicMock(return_value=json_data or {})
    return resp


def _make_httpx_client(get_response=None, post_response=None):
    """Build a mock httpx.AsyncClient context manager."""
    client = MagicMock()
    client.get = AsyncMock(return_value=get_response or _make_httpx_response())
    client.post = AsyncMock(return_value=post_response or _make_httpx_response())
    client.__aenter__ = AsyncMock(return_value=client)
    client.__aexit__ = AsyncMock(return_value=None)
    return client


MODELS_RESPONSE = {"models": [{"name": "mistral:latest"}, {"name": "llama3.2:3b"}]}


# ---------------------------------------------------------------------------
# GET /api/ollama/health
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestCheckOllamaHealth:
    def test_returns_200_when_ollama_running(self):
        resp = _make_httpx_response(200, MODELS_RESPONSE)
        client = _make_httpx_client(get_response=resp)
        with patch("routes.ollama_routes.httpx.AsyncClient", return_value=client):
            tc = TestClient(_build_app())
            response = tc.get("/api/ollama/health")
        assert response.status_code == 200

    def test_connected_true_when_running(self):
        resp = _make_httpx_response(200, MODELS_RESPONSE)
        client = _make_httpx_client(get_response=resp)
        with patch("routes.ollama_routes.httpx.AsyncClient", return_value=client):
            tc = TestClient(_build_app())
            data = tc.get("/api/ollama/health").json()
        assert data["connected"] is True
        assert len(data["models"]) == 2

    def test_connected_false_when_unreachable(self):
        client = _make_httpx_client()
        client.get = AsyncMock(side_effect=httpx.ConnectError("refused"))
        with patch("routes.ollama_routes.httpx.AsyncClient", return_value=client):
            tc = TestClient(_build_app())
            data = tc.get("/api/ollama/health").json()
        assert data["connected"] is False
        assert data["status"] == "unreachable"

    def test_connected_false_on_timeout(self):
        client = _make_httpx_client()
        client.get = AsyncMock(side_effect=httpx.TimeoutException("timed out"))
        with patch("routes.ollama_routes.httpx.AsyncClient", return_value=client):
            tc = TestClient(_build_app())
            data = tc.get("/api/ollama/health").json()
        assert data["connected"] is False
        assert data["status"] == "timeout"

    def test_requires_auth(self):
        app = FastAPI()
        app.include_router(router)
        tc = TestClient(app, raise_server_exceptions=False)
        resp = tc.get("/api/ollama/health")
        assert resp.status_code == 401


# ---------------------------------------------------------------------------
# GET /api/ollama/models
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestGetOllamaModels:
    def test_returns_200_when_running(self):
        resp = _make_httpx_response(200, MODELS_RESPONSE)
        client = _make_httpx_client(get_response=resp)
        with patch("routes.ollama_routes.httpx.AsyncClient", return_value=client):
            tc = TestClient(_build_app())
            response = tc.get("/api/ollama/models")
        assert response.status_code == 200

    def test_returns_models_list_when_connected(self):
        resp = _make_httpx_response(200, MODELS_RESPONSE)
        client = _make_httpx_client(get_response=resp)
        with patch("routes.ollama_routes.httpx.AsyncClient", return_value=client):
            tc = TestClient(_build_app())
            data = tc.get("/api/ollama/models").json()
        assert data["connected"] is True
        assert isinstance(data["models"], list)

    def test_returns_defaults_when_unreachable(self):
        client = _make_httpx_client()
        client.get = AsyncMock(side_effect=httpx.ConnectError("refused"))
        with patch("routes.ollama_routes.httpx.AsyncClient", return_value=client):
            tc = TestClient(_build_app())
            data = tc.get("/api/ollama/models").json()
        assert data["connected"] is False
        assert isinstance(data["models"], list)
        # Route returns empty list when Ollama is unreachable (honest response, no defaults)


# ---------------------------------------------------------------------------
# POST /api/ollama/warmup
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestWarmupOllama:
    def test_returns_200_on_success(self):
        tags_resp = _make_httpx_response(200, {"models": [{"name": "mistral:latest"}]})
        warmup_resp = _make_httpx_response(200, {"total_duration": 2_000_000_000})
        client = _make_httpx_client(get_response=tags_resp, post_response=warmup_resp)
        with patch("routes.ollama_routes.httpx.AsyncClient", return_value=client):
            tc = TestClient(_build_app())
            resp = tc.post("/api/ollama/warmup?model=mistral:latest")
        assert resp.status_code == 200

    def test_success_status_when_model_available(self):
        tags_resp = _make_httpx_response(200, {"models": [{"name": "mistral:latest"}]})
        warmup_resp = _make_httpx_response(200, {"total_duration": 2_000_000_000})
        client = _make_httpx_client(get_response=tags_resp, post_response=warmup_resp)
        with patch("routes.ollama_routes.httpx.AsyncClient", return_value=client):
            tc = TestClient(_build_app())
            data = tc.post("/api/ollama/warmup?model=mistral:latest").json()
        assert data["status"] == "success"

    def test_warning_when_model_not_available(self):
        tags_resp = _make_httpx_response(200, {"models": [{"name": "llama3.2:3b"}]})
        client = _make_httpx_client(get_response=tags_resp)
        with patch("routes.ollama_routes.httpx.AsyncClient", return_value=client):
            tc = TestClient(_build_app())
            data = tc.post("/api/ollama/warmup?model=nonexistent-model").json()
        assert data["status"] == "warning"

    def test_error_when_ollama_not_running(self):
        client = _make_httpx_client()
        client.get = AsyncMock(side_effect=httpx.ConnectError("refused"))
        with patch("routes.ollama_routes.httpx.AsyncClient", return_value=client):
            tc = TestClient(_build_app())
            data = tc.post("/api/ollama/warmup").json()
        assert data["status"] == "error"

    def test_timeout_returns_warning(self):
        client = _make_httpx_client()
        client.get = AsyncMock(side_effect=httpx.TimeoutException("timeout"))
        with patch("routes.ollama_routes.httpx.AsyncClient", return_value=client):
            tc = TestClient(_build_app())
            data = tc.post("/api/ollama/warmup").json()
        assert data["status"] == "warning"


# ---------------------------------------------------------------------------
# GET /api/ollama/status
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestGetOllamaStatus:
    def test_returns_200(self):
        resp = _make_httpx_response(200, MODELS_RESPONSE)
        client = _make_httpx_client(get_response=resp)
        with patch("routes.ollama_routes.httpx.AsyncClient", return_value=client):
            tc = TestClient(_build_app())
            response = tc.get("/api/ollama/status")
        assert response.status_code == 200

    def test_running_true_when_connected(self):
        resp = _make_httpx_response(200, MODELS_RESPONSE)
        client = _make_httpx_client(get_response=resp)
        with patch("routes.ollama_routes.httpx.AsyncClient", return_value=client):
            tc = TestClient(_build_app())
            data = tc.get("/api/ollama/status").json()
        assert data["running"] is True

    def test_running_false_when_unreachable(self):
        client = _make_httpx_client()
        client.get = AsyncMock(side_effect=httpx.ConnectError("refused"))
        with patch("routes.ollama_routes.httpx.AsyncClient", return_value=client):
            tc = TestClient(_build_app())
            data = tc.get("/api/ollama/status").json()
        assert data["running"] is False

    def test_response_has_required_fields(self):
        resp = _make_httpx_response(200, MODELS_RESPONSE)
        client = _make_httpx_client(get_response=resp)
        with patch("routes.ollama_routes.httpx.AsyncClient", return_value=client):
            tc = TestClient(_build_app())
            data = tc.get("/api/ollama/status").json()
        for field in ["running", "host", "models_available", "models", "last_check"]:
            assert field in data


# ---------------------------------------------------------------------------
# POST /api/ollama/select-model
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestSelectOllamaModel:
    def test_returns_200(self):
        resp = _make_httpx_response(200, {"models": [{"name": "mistral:latest"}]})
        client = _make_httpx_client(get_response=resp)
        with patch("routes.ollama_routes.httpx.AsyncClient", return_value=client):
            tc = TestClient(_build_app())
            response = tc.get("/api/ollama/select-model?model=mistral:latest")
        assert response.status_code == 200

    def test_success_when_model_available(self):
        resp = _make_httpx_response(200, {"models": [{"name": "mistral:latest"}]})
        client = _make_httpx_client(get_response=resp)
        with patch("routes.ollama_routes.httpx.AsyncClient", return_value=client):
            tc = TestClient(_build_app())
            data = tc.get("/api/ollama/select-model?model=mistral:latest").json()
        assert data["success"] is True

    def test_missing_model_returns_422(self):
        tc = TestClient(_build_app())
        resp = tc.get("/api/ollama/select-model")
        assert resp.status_code == 422

    def test_model_not_found_returns_success_false(self):
        resp = _make_httpx_response(200, {"models": [{"name": "llama3.2:3b"}]})
        client = _make_httpx_client(get_response=resp)
        with patch("routes.ollama_routes.httpx.AsyncClient", return_value=client):
            tc = TestClient(_build_app())
            data = tc.get("/api/ollama/select-model?model=nonexistent").json()
        assert data["success"] is False

    def test_non_200_from_ollama_returns_success_false(self):
        resp = _make_httpx_response(503, {})
        client = _make_httpx_client(get_response=resp)
        with patch("routes.ollama_routes.httpx.AsyncClient", return_value=client):
            tc = TestClient(_build_app())
            data = tc.get("/api/ollama/select-model?model=mistral:latest").json()
        assert data["success"] is False

    def test_exception_returns_success_false(self):
        client = _make_httpx_client()
        client.get = AsyncMock(side_effect=RuntimeError("Unexpected error"))
        with patch("routes.ollama_routes.httpx.AsyncClient", return_value=client):
            tc = TestClient(_build_app())
            data = tc.get("/api/ollama/select-model?model=mistral:latest").json()
        assert data["success"] is False

    def test_requires_auth(self):
        app = FastAPI()
        app.include_router(router)
        tc = TestClient(app, raise_server_exceptions=False)
        assert tc.get("/api/ollama/select-model?model=mistral:latest").status_code == 401


# ---------------------------------------------------------------------------
# Auth enforcement — remaining endpoints
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestAuthEnforcement:
    """Verify all endpoints except health require authentication."""

    def _no_auth_app(self):
        app = FastAPI()
        app.include_router(router)
        return TestClient(app, raise_server_exceptions=False)

    def test_get_models_requires_auth(self):
        assert self._no_auth_app().get("/api/ollama/models").status_code == 401

    def test_warmup_requires_auth(self):
        assert self._no_auth_app().post("/api/ollama/warmup").status_code == 401

    def test_get_status_requires_auth(self):
        assert self._no_auth_app().get("/api/ollama/status").status_code == 401


# ---------------------------------------------------------------------------
# Additional error paths
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestGetOllamaModelsErrorPaths:
    def test_non_200_response_returns_connected_false(self):
        resp = _make_httpx_response(503, {})
        client = _make_httpx_client(get_response=resp)
        with patch("routes.ollama_routes.httpx.AsyncClient", return_value=client):
            tc = TestClient(_build_app())
            data = tc.get("/api/ollama/models").json()
        assert data["connected"] is False
        assert data["models"] == []

    def test_generic_exception_returns_connected_false(self):
        client = _make_httpx_client()
        client.get = AsyncMock(side_effect=RuntimeError("Unexpected"))
        with patch("routes.ollama_routes.httpx.AsyncClient", return_value=client):
            tc = TestClient(_build_app())
            data = tc.get("/api/ollama/models").json()
        assert data["connected"] is False


@pytest.mark.unit
class TestWarmupOllamaErrorPaths:
    def test_health_check_non_200_returns_error(self):
        """When /api/tags returns non-200, warmup returns error status."""
        resp = _make_httpx_response(503, {})
        client = _make_httpx_client(get_response=resp)
        with patch("routes.ollama_routes.httpx.AsyncClient", return_value=client):
            tc = TestClient(_build_app())
            data = tc.post("/api/ollama/warmup?model=mistral:latest").json()
        assert data["status"] == "error"

    def test_warmup_post_non_200_returns_error(self):
        """When /api/generate POST returns non-200, warmup returns error status."""
        tags_resp = _make_httpx_response(200, {"models": [{"name": "mistral:latest"}]})
        warmup_resp = _make_httpx_response(500, {})
        client = _make_httpx_client(get_response=tags_resp, post_response=warmup_resp)
        with patch("routes.ollama_routes.httpx.AsyncClient", return_value=client):
            tc = TestClient(_build_app())
            data = tc.post("/api/ollama/warmup?model=mistral:latest").json()
        assert data["status"] == "error"


@pytest.mark.unit
class TestGetOllamaStatusErrorPaths:
    def test_non_200_response_returns_running_false(self):
        resp = _make_httpx_response(503, {})
        client = _make_httpx_client(get_response=resp)
        with patch("routes.ollama_routes.httpx.AsyncClient", return_value=client):
            tc = TestClient(_build_app())
            data = tc.get("/api/ollama/status").json()
        assert data["running"] is False
