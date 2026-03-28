"""
Unit tests for routes/profiling_routes.py.

Tests cover:
- GET /api/profiling/slow-endpoints  — get_slow_endpoints
- GET /api/profiling/endpoint-stats  — get_endpoint_stats
- GET /api/profiling/recent-requests — get_recent_requests
- GET /api/profiling/phase-breakdown — get_phase_breakdown
- GET /api/profiling/health          — profiling_health

profiling_middleware module-level variable is patched for each test.
Auth is router-level (dependencies=[Depends(get_current_user)]).
"""

from unittest.mock import MagicMock

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

import routes.profiling_routes as profiling_module
from middleware.api_token_auth import verify_api_token
from routes.profiling_routes import router
from tests.unit.routes.conftest import TEST_USER


def _make_middleware():
    mw = MagicMock()
    mw.get_slow_endpoints = MagicMock(return_value={})
    mw.get_endpoint_stats = MagicMock(return_value={})
    mw.get_recent_profiles = MagicMock(return_value=[])
    mw.profiles = []
    mw.slow_endpoints = {}
    mw.max_profiles = 1000
    return mw


def _build_app() -> FastAPI:
    app = FastAPI()
    app.include_router(router)
    app.dependency_overrides[verify_api_token] = lambda: "test-token"
    return app


@pytest.fixture(autouse=True)
def reset_middleware():
    """Reset global middleware reference after each test."""
    original = profiling_module.profiling_middleware
    yield
    profiling_module.profiling_middleware = original


# ---------------------------------------------------------------------------
# GET /api/profiling/slow-endpoints
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestGetSlowEndpoints:
    def test_returns_503_when_not_initialized(self):
        profiling_module.profiling_middleware = None
        client = TestClient(_build_app())
        resp = client.get("/api/profiling/slow-endpoints")
        assert resp.status_code == 503

    def test_returns_200_when_initialized(self):
        profiling_module.profiling_middleware = _make_middleware()
        client = TestClient(_build_app())
        resp = client.get("/api/profiling/slow-endpoints")
        assert resp.status_code == 200

    def test_response_has_required_fields(self):
        profiling_module.profiling_middleware = _make_middleware()
        client = TestClient(_build_app())
        data = client.get("/api/profiling/slow-endpoints").json()
        assert "threshold_ms" in data
        assert "slow_endpoints" in data
        assert "count" in data

    def test_custom_threshold_accepted(self):
        profiling_module.profiling_middleware = _make_middleware()
        client = TestClient(_build_app())
        resp = client.get("/api/profiling/slow-endpoints?threshold_ms=500")
        assert resp.status_code == 200
        data = resp.json()
        assert data["threshold_ms"] == 500

    def test_requires_auth(self):
        profiling_module.profiling_middleware = _make_middleware()
        app = FastAPI()
        app.include_router(router)
        client = TestClient(app, raise_server_exceptions=False)
        resp = client.get("/api/profiling/slow-endpoints")
        assert resp.status_code == 401


# ---------------------------------------------------------------------------
# GET /api/profiling/endpoint-stats
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestGetEndpointStats:
    def test_returns_503_when_not_initialized(self):
        profiling_module.profiling_middleware = None
        client = TestClient(_build_app())
        resp = client.get("/api/profiling/endpoint-stats")
        assert resp.status_code == 503

    def test_returns_200_when_initialized(self):
        profiling_module.profiling_middleware = _make_middleware()
        client = TestClient(_build_app())
        resp = client.get("/api/profiling/endpoint-stats")
        assert resp.status_code == 200

    def test_response_has_timestamp_and_endpoints(self):
        profiling_module.profiling_middleware = _make_middleware()
        client = TestClient(_build_app())
        data = client.get("/api/profiling/endpoint-stats").json()
        assert "timestamp" in data
        assert "endpoint_count" in data
        assert "endpoints" in data


# ---------------------------------------------------------------------------
# GET /api/profiling/recent-requests
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestGetRecentRequests:
    def test_returns_503_when_not_initialized(self):
        profiling_module.profiling_middleware = None
        client = TestClient(_build_app())
        resp = client.get("/api/profiling/recent-requests")
        assert resp.status_code == 503

    def test_returns_200_when_initialized(self):
        profiling_module.profiling_middleware = _make_middleware()
        client = TestClient(_build_app())
        resp = client.get("/api/profiling/recent-requests")
        assert resp.status_code == 200

    def test_response_has_profiles(self):
        profiling_module.profiling_middleware = _make_middleware()
        client = TestClient(_build_app())
        data = client.get("/api/profiling/recent-requests").json()
        assert "profiles" in data
        assert "count" in data

    def test_limit_param_accepted(self):
        profiling_module.profiling_middleware = _make_middleware()
        client = TestClient(_build_app())
        resp = client.get("/api/profiling/recent-requests?limit=50")
        assert resp.status_code == 200


# ---------------------------------------------------------------------------
# GET /api/profiling/phase-breakdown
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestGetPhaseBreakdown:
    def test_returns_503_when_not_initialized(self):
        profiling_module.profiling_middleware = None
        client = TestClient(_build_app())
        resp = client.get("/api/profiling/phase-breakdown")
        assert resp.status_code == 503

    def test_returns_200_when_initialized(self):
        profiling_module.profiling_middleware = _make_middleware()
        client = TestClient(_build_app())
        resp = client.get("/api/profiling/phase-breakdown")
        assert resp.status_code == 200

    def test_response_has_required_fields(self):
        profiling_module.profiling_middleware = _make_middleware()
        client = TestClient(_build_app())
        data = client.get("/api/profiling/phase-breakdown").json()
        assert "phase_breakdown" in data
        assert "slow_phases" in data
        assert "slow_phase_count" in data


# ---------------------------------------------------------------------------
# GET /api/profiling/health
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestProfilingHealth:
    def test_returns_200_when_not_initialized(self):
        profiling_module.profiling_middleware = None
        client = TestClient(_build_app())
        resp = client.get("/api/profiling/health")
        assert resp.status_code == 200

    def test_returns_not_initialized_status(self):
        profiling_module.profiling_middleware = None
        client = TestClient(_build_app())
        data = client.get("/api/profiling/health").json()
        assert data["status"] == "not_initialized"

    def test_returns_healthy_when_initialized(self):
        profiling_module.profiling_middleware = _make_middleware()
        client = TestClient(_build_app())
        data = client.get("/api/profiling/health").json()
        assert data["status"] == "healthy"

    def test_health_response_has_profile_counts(self):
        profiling_module.profiling_middleware = _make_middleware()
        client = TestClient(_build_app())
        data = client.get("/api/profiling/health").json()
        assert "profiles_tracked" in data
        assert "slow_endpoints_detected" in data
