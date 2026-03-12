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

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from unittest.mock import AsyncMock, MagicMock, patch

import httpx

from routes.auth_unified import get_current_user
from routes.ollama_routes import router

from tests.unit.routes.conftest import TEST_USER


def _build_app() -> FastAPI:
    app = FastAPI()
    app.include_router(router)
    app.dependency_overrides[get_current_user] = lambda: TEST_USER
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
        assert len(data["models"]) > 0  # Returns default models


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
            response = tc.post(
                "/api/ollama/select-model", json={"model": "mistral:latest"}
            )
        assert response.status_code == 200

    def test_success_when_model_available(self):
        resp = _make_httpx_response(200, {"models": [{"name": "mistral:latest"}]})
        client = _make_httpx_client(get_response=resp)
        with patch("routes.ollama_routes.httpx.AsyncClient", return_value=client):
            tc = TestClient(_build_app())
            data = tc.post(
                "/api/ollama/select-model", json={"model": "mistral:latest"}
            ).json()
        assert data["success"] is True

    def test_missing_model_returns_422(self):
        tc = TestClient(_build_app())
        resp = tc.post("/api/ollama/select-model", json={})
        assert resp.status_code == 422
