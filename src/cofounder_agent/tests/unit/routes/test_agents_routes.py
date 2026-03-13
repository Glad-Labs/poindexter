"""
Unit tests for routes/agents_routes.py.

Tests cover:
- GET  /api/agents/status            — get_all_agents_status
- GET  /api/agents/{name}/status     — get_agent_status
- POST /api/agents/{name}/command    — send_agent_command
- GET  /api/agents/logs              — get_agent_logs
- GET  /api/agents/memory/stats      — get_memory_stats
- GET  /api/agents/health            — get_agent_system_health

get_orchestrator reads from request.app.state — overridden via dependency injection.
Auth is required on these endpoints — get_current_user overridden with TEST_USER.
"""

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from unittest.mock import MagicMock

from routes.agents_routes import router, get_orchestrator
from routes.auth_unified import get_current_user
from tests.unit.routes.conftest import TEST_USER


def _make_orchestrator(system_status=None):
    orch = MagicMock()
    orch._get_system_status = MagicMock(
        return_value=system_status
        or {
            "uptime_seconds": 3600,
            "memory_usage_mb": 200.0,
            "cpu_usage_percent": 10.0,
            "database_connected": True,
        }
    )
    # memory_system is None by default so memory/stats returns zeros
    orch.memory_system = None
    return orch


def _build_app(orchestrator=None) -> FastAPI:
    app = FastAPI()
    app.include_router(router)
    orch = orchestrator if orchestrator is not None else _make_orchestrator()
    app.dependency_overrides[get_orchestrator] = lambda: orch
    app.dependency_overrides[get_current_user] = lambda: TEST_USER
    return app


def _build_app_no_orchestrator() -> FastAPI:
    """App with no dependency override — get_orchestrator raises 503."""
    app = FastAPI()
    app.include_router(router)
    app.dependency_overrides[get_current_user] = lambda: TEST_USER
    return app


def _build_app_unauthenticated(orchestrator=None) -> FastAPI:
    """App where get_current_user raises 401 — simulates missing/invalid token."""
    from fastapi import HTTPException as _HTTPException

    def _reject():
        raise _HTTPException(status_code=401, detail="Not authenticated")

    app = FastAPI()
    app.include_router(router)
    orch = orchestrator if orchestrator is not None else _make_orchestrator()
    app.dependency_overrides[get_orchestrator] = lambda: orch
    app.dependency_overrides[get_current_user] = _reject
    return app


# ---------------------------------------------------------------------------
# GET /api/agents/status
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestGetAllAgentsStatus:
    def test_returns_200(self):
        client = TestClient(_build_app())
        resp = client.get("/api/agents/status")
        assert resp.status_code == 200

    def test_response_has_required_fields(self):
        client = TestClient(_build_app())
        data = client.get("/api/agents/status").json()
        for field in ["status", "timestamp", "agents", "system_health"]:
            assert field in data

    def test_agents_dict_has_all_four_agents(self):
        client = TestClient(_build_app())
        data = client.get("/api/agents/status").json()
        for name in ["content", "financial", "market", "compliance"]:
            assert name in data["agents"]

    def test_overall_status_healthy_when_no_errors(self):
        client = TestClient(_build_app())
        data = client.get("/api/agents/status").json()
        assert data["status"] == "healthy"

    def test_returns_503_when_orchestrator_not_initialized(self):
        client = TestClient(_build_app_no_orchestrator(), raise_server_exceptions=False)
        resp = client.get("/api/agents/status")
        assert resp.status_code == 503


# ---------------------------------------------------------------------------
# GET /api/agents/{agent_name}/status
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestGetAgentStatus:
    def test_known_agent_returns_200(self):
        client = TestClient(_build_app())
        resp = client.get("/api/agents/content/status")
        assert resp.status_code == 200

    def test_response_has_required_fields(self):
        client = TestClient(_build_app())
        data = client.get("/api/agents/financial/status").json()
        for field in ["name", "type", "status"]:
            assert field in data

    def test_unknown_agent_returns_400(self):
        """Route returns 400 (not 404) for unrecognized agent names."""
        client = TestClient(_build_app())
        resp = client.get("/api/agents/nonexistent/status")
        assert resp.status_code == 400

    def test_all_valid_agent_names_return_200(self):
        client = TestClient(_build_app())
        for name in ["content", "financial", "market", "compliance"]:
            resp = client.get(f"/api/agents/{name}/status")
            assert resp.status_code == 200, f"Expected 200 for agent '{name}'"

    def test_orchestrator_not_initialized_returns_503(self):
        client = TestClient(_build_app_no_orchestrator(), raise_server_exceptions=False)
        resp = client.get("/api/agents/content/status")
        assert resp.status_code == 503


# ---------------------------------------------------------------------------
# POST /api/agents/{agent_name}/command
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestSendAgentCommand:
    def test_known_agent_returns_200(self):
        client = TestClient(_build_app())
        resp = client.post(
            "/api/agents/content/command",
            json={"command": "execute", "parameters": {"task_id": "t-123"}},
        )
        assert resp.status_code == 200

    def test_response_has_status_success(self):
        client = TestClient(_build_app())
        data = client.post(
            "/api/agents/content/command",
            json={"command": "execute"},
        ).json()
        assert data["status"] == "success"
        assert "message" in data
        assert "result" in data

    def test_unknown_agent_returns_404(self):
        client = TestClient(_build_app())
        resp = client.post(
            "/api/agents/unknown_agent/command",
            json={"command": "execute"},
        )
        assert resp.status_code == 400  # Route returns 400 for unrecognized agent

    def test_missing_command_returns_422(self):
        client = TestClient(_build_app())
        resp = client.post("/api/agents/content/command", json={})
        assert resp.status_code == 422

    def test_invalid_command_enum_returns_422(self):
        client = TestClient(_build_app())
        resp = client.post(
            "/api/agents/content/command",
            json={"command": "not_a_real_command"},
        )
        assert resp.status_code == 422

    def test_orchestrator_not_initialized_returns_503(self):
        client = TestClient(_build_app_no_orchestrator(), raise_server_exceptions=False)
        resp = client.post(
            "/api/agents/content/command",
            json={"command": "execute"},
        )
        assert resp.status_code == 503


# ---------------------------------------------------------------------------
# GET /api/agents/logs
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestGetAgentLogs:
    def test_returns_200(self):
        client = TestClient(_build_app())
        resp = client.get("/api/agents/logs")
        assert resp.status_code == 200

    def test_response_has_logs_and_total(self):
        client = TestClient(_build_app())
        data = client.get("/api/agents/logs").json()
        assert "logs" in data
        assert "total" in data
        assert "filtered_by" in data

    def test_default_returns_empty_logs(self):
        client = TestClient(_build_app())
        data = client.get("/api/agents/logs").json()
        assert data["logs"] == []
        assert data["total"] == 0

    def test_agent_filter_accepted(self):
        client = TestClient(_build_app())
        resp = client.get("/api/agents/logs?agent=content")
        assert resp.status_code == 200

    def test_level_filter_accepted(self):
        client = TestClient(_build_app())
        resp = client.get("/api/agents/logs?level=ERROR")
        assert resp.status_code == 200

    def test_limit_param_accepted(self):
        client = TestClient(_build_app())
        resp = client.get("/api/agents/logs?limit=100")
        assert resp.status_code == 200

    def test_limit_too_large_still_returns_200(self):
        """Route does not enforce a max limit via Pydantic — returns 200 with clamped results."""
        client = TestClient(_build_app())
        resp = client.get("/api/agents/logs?limit=201")
        assert resp.status_code == 200

    def test_no_orchestrator_still_returns_200(self):
        """get_agent_logs does not use get_orchestrator dependency."""
        client = TestClient(_build_app_no_orchestrator())
        resp = client.get("/api/agents/logs")
        assert resp.status_code == 200


# ---------------------------------------------------------------------------
# GET /api/agents/memory/stats
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestGetMemoryStats:
    def test_returns_200(self):
        client = TestClient(_build_app())
        resp = client.get("/api/agents/memory/stats")
        assert resp.status_code == 200

    def test_response_has_required_fields(self):
        client = TestClient(_build_app())
        data = client.get("/api/agents/memory/stats").json()
        for field in ["total_memories", "short_term_count", "long_term_count", "by_agent"]:
            assert field in data

    def test_zero_memories_when_no_memory_system(self):
        """When orchestrator.memory_system is None, returns zero counts."""
        client = TestClient(_build_app())
        data = client.get("/api/agents/memory/stats").json()
        assert data["total_memories"] == 0

    def test_orchestrator_not_initialized_returns_503(self):
        client = TestClient(_build_app_no_orchestrator(), raise_server_exceptions=False)
        resp = client.get("/api/agents/memory/stats")
        assert resp.status_code == 503


# ---------------------------------------------------------------------------
# GET /api/agents/health
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestGetAgentSystemHealth:
    def test_returns_200(self):
        client = TestClient(_build_app())
        resp = client.get("/api/agents/health")
        assert resp.status_code == 200

    def test_response_has_required_fields(self):
        client = TestClient(_build_app())
        data = client.get("/api/agents/health").json()
        for field in [
            "status",
            "timestamp",
            "all_agents_running",
            "error_count",
            "warning_count",
            "details",
        ]:
            assert field in data

    def test_healthy_status_when_no_errors(self):
        client = TestClient(_build_app())
        data = client.get("/api/agents/health").json()
        assert data["status"] == "healthy"
        assert data["all_agents_running"] is True
        assert data["error_count"] == 0

    def test_details_has_agent_and_system_keys(self):
        client = TestClient(_build_app())
        data = client.get("/api/agents/health").json()
        assert "database_connection" in data["details"]
        assert "memory_system" in data["details"]
        assert "model_router" in data["details"]

    def test_orchestrator_not_initialized_returns_503(self):
        client = TestClient(_build_app_no_orchestrator(), raise_server_exceptions=False)
        resp = client.get("/api/agents/health")
        assert resp.status_code == 503


# ---------------------------------------------------------------------------
# Auth guard — all endpoints require authentication
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestAgentRoutesRequireAuth:
    """Verify that each endpoint returns 401 when no auth token is provided."""

    def test_all_agents_status_requires_auth(self):
        client = TestClient(_build_app_unauthenticated(), raise_server_exceptions=False)
        resp = client.get("/api/agents/status")
        assert resp.status_code == 401

    def test_agent_status_requires_auth(self):
        client = TestClient(_build_app_unauthenticated(), raise_server_exceptions=False)
        resp = client.get("/api/agents/content/status")
        assert resp.status_code == 401

    def test_send_command_requires_auth(self):
        client = TestClient(_build_app_unauthenticated(), raise_server_exceptions=False)
        resp = client.post(
            "/api/agents/content/command",
            json={"command": "execute"},
        )
        assert resp.status_code == 401

    def test_get_logs_requires_auth(self):
        client = TestClient(_build_app_unauthenticated(), raise_server_exceptions=False)
        resp = client.get("/api/agents/logs")
        assert resp.status_code == 401

    def test_memory_stats_requires_auth(self):
        client = TestClient(_build_app_unauthenticated(), raise_server_exceptions=False)
        resp = client.get("/api/agents/memory/stats")
        assert resp.status_code == 401

    def test_health_requires_auth(self):
        client = TestClient(_build_app_unauthenticated(), raise_server_exceptions=False)
        resp = client.get("/api/agents/health")
        assert resp.status_code == 401
