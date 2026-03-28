"""
Unit tests for routes/agent_registry_routes.py.

Tests cover:
- GET /api/agents/registry             — get_agent_registry_endpoint
- GET /api/agents/list                 — list_agents
- GET /api/agents/{agent_name}         — get_agent_metadata
- GET /api/agents/{agent_name}/phases  — get_agent_phases
- GET /api/agents/{agent_name}/capabilities — get_agent_capabilities
- GET /api/agents/by-phase/{phase}     — get_agents_by_phase
- GET /api/agents/by-capability/{cap}  — get_agents_by_capability
- GET /api/agents/by-category/{cat}    — get_agents_by_category
- GET /api/agents/search               — search_agents

get_agent_registry is patched to avoid real registry I/O.
Auth is required on these endpoints — get_current_user overridden with TEST_USER.
"""

from unittest.mock import MagicMock, patch

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from routes.agent_registry_routes import router
from middleware.api_token_auth import verify_api_token
from tests.unit.routes.conftest import TEST_USER

SAMPLE_AGENTS = [
    {
        "name": "research_agent",
        "category": "content",
        "phases": ["research"],
        "capabilities": ["web_search", "summarization"],
        "description": "Gathers research and background information",
        "version": "1.0",
    },
    {
        "name": "creative_agent",
        "category": "content",
        "phases": ["draft", "refine"],
        "capabilities": ["content_generation", "writing"],
        "description": "Generates creative content",
        "version": "1.0",
    },
    {
        "name": "financial_agent",
        "category": "financial",
        "phases": ["analysis"],
        "capabilities": ["financial_analysis"],
        "description": "Handles financial analysis tasks",
        "version": "1.0",
    },
]


def _make_registry(agents=None, agent_obj=None):
    reg = MagicMock()
    _agents = agents if agents is not None else SAMPLE_AGENTS
    reg.list_all_with_metadata = MagicMock(return_value=_agents)
    reg.list_agents = MagicMock(return_value=[a["name"] for a in _agents])
    reg.list_categories = MagicMock(
        return_value={
            "content": ["research_agent", "creative_agent"],
            "financial": ["financial_agent"],
        }
    )
    reg.list_by_phase = MagicMock(return_value=["creative_agent"])
    reg.list_by_capability = MagicMock(return_value=["research_agent"])

    # get_serializable_metadata returns the agent dict or None
    def _get_meta(name):
        for a in _agents:
            if a["name"] == name:
                return a
        return None

    reg.get_serializable_metadata = MagicMock(side_effect=_get_meta)
    reg.get_phases = MagicMock(
        side_effect=lambda name: next((a["phases"] for a in _agents if a["name"] == name), None)
    )
    reg.get_capabilities = MagicMock(
        side_effect=lambda name: next(
            (a["capabilities"] for a in _agents if a["name"] == name), None
        )
    )
    return reg


def _build_app() -> FastAPI:
    app = FastAPI()
    app.include_router(router)
    app.dependency_overrides[verify_api_token] = lambda: "test-token"
    return app


def _build_app_unauthenticated() -> FastAPI:
    """App where get_current_user raises 401 — simulates missing/invalid token."""
    from fastapi import HTTPException as _HTTPException

    def _reject():
        raise _HTTPException(status_code=401, detail="Not authenticated")

    app = FastAPI()
    app.include_router(router)
    app.dependency_overrides[verify_api_token] = _reject
    return app


# ---------------------------------------------------------------------------
# GET /api/agents/registry
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestGetAgentRegistryEndpoint:
    def test_returns_200(self):
        with patch(
            "routes.agent_registry_routes.get_agent_registry",
            return_value=_make_registry(),
        ):
            client = TestClient(_build_app())
            resp = client.get("/api/agents/registry")
        assert resp.status_code == 200

    def test_response_has_required_keys(self):
        with patch(
            "routes.agent_registry_routes.get_agent_registry",
            return_value=_make_registry(),
        ):
            client = TestClient(_build_app())
            data = client.get("/api/agents/registry").json()
        for key in ["agents", "total_agents", "categories", "phases"]:
            assert key in data

    def test_total_agents_matches_agents_list(self):
        with patch(
            "routes.agent_registry_routes.get_agent_registry",
            return_value=_make_registry(),
        ):
            client = TestClient(_build_app())
            data = client.get("/api/agents/registry").json()
        assert data["total_agents"] == len(data["agents"])

    def test_registry_error_returns_500(self):
        with patch(
            "routes.agent_registry_routes.get_agent_registry",
            side_effect=RuntimeError("registry down"),
        ):
            client = TestClient(_build_app(), raise_server_exceptions=False)
            resp = client.get("/api/agents/registry")
        assert resp.status_code == 500


# ---------------------------------------------------------------------------
# GET /api/agents/list
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestListAgents:
    def test_returns_200(self):
        with patch(
            "routes.agent_registry_routes.get_agent_registry",
            return_value=_make_registry(),
        ):
            client = TestClient(_build_app())
            resp = client.get("/api/agents/list")
        assert resp.status_code == 200

    def test_response_is_list_of_strings(self):
        with patch(
            "routes.agent_registry_routes.get_agent_registry",
            return_value=_make_registry(),
        ):
            client = TestClient(_build_app())
            data = client.get("/api/agents/list").json()
        assert isinstance(data, list)
        assert all(isinstance(name, str) for name in data)

    def test_registry_error_returns_500(self):
        with patch(
            "routes.agent_registry_routes.get_agent_registry",
            side_effect=RuntimeError("registry down"),
        ):
            client = TestClient(_build_app(), raise_server_exceptions=False)
            resp = client.get("/api/agents/list")
        assert resp.status_code == 500


# ---------------------------------------------------------------------------
# GET /api/agents/{agent_name}
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestGetAgentMetadata:
    def test_existing_agent_returns_200(self):
        with patch(
            "routes.agent_registry_routes.get_agent_registry",
            return_value=_make_registry(),
        ):
            client = TestClient(_build_app())
            resp = client.get("/api/agents/research_agent")
        assert resp.status_code == 200

    def test_response_has_agent_fields(self):
        with patch(
            "routes.agent_registry_routes.get_agent_registry",
            return_value=_make_registry(),
        ):
            client = TestClient(_build_app())
            data = client.get("/api/agents/research_agent").json()
        assert "name" in data
        assert data["name"] == "research_agent"

    def test_unknown_agent_returns_404(self):
        with patch(
            "routes.agent_registry_routes.get_agent_registry",
            return_value=_make_registry(),
        ):
            client = TestClient(_build_app())
            resp = client.get("/api/agents/nonexistent_agent")
        assert resp.status_code == 404

    def test_registry_error_returns_500(self):
        with patch(
            "routes.agent_registry_routes.get_agent_registry",
            side_effect=RuntimeError("registry down"),
        ):
            client = TestClient(_build_app(), raise_server_exceptions=False)
            resp = client.get("/api/agents/any_agent")
        assert resp.status_code == 500


# ---------------------------------------------------------------------------
# GET /api/agents/{agent_name}/phases
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestGetAgentPhases:
    def test_existing_agent_returns_200(self):
        with patch(
            "routes.agent_registry_routes.get_agent_registry",
            return_value=_make_registry(),
        ):
            client = TestClient(_build_app())
            resp = client.get("/api/agents/creative_agent/phases")
        assert resp.status_code == 200

    def test_response_is_list_of_phases(self):
        with patch(
            "routes.agent_registry_routes.get_agent_registry",
            return_value=_make_registry(),
        ):
            client = TestClient(_build_app())
            data = client.get("/api/agents/creative_agent/phases").json()
        assert isinstance(data, list)
        assert "draft" in data

    def test_unknown_agent_returns_404(self):
        with patch(
            "routes.agent_registry_routes.get_agent_registry",
            return_value=_make_registry(),
        ):
            client = TestClient(_build_app())
            resp = client.get("/api/agents/nonexistent_agent/phases")
        assert resp.status_code == 404


# ---------------------------------------------------------------------------
# GET /api/agents/{agent_name}/capabilities
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestGetAgentCapabilities:
    def test_existing_agent_returns_200(self):
        with patch(
            "routes.agent_registry_routes.get_agent_registry",
            return_value=_make_registry(),
        ):
            client = TestClient(_build_app())
            resp = client.get("/api/agents/research_agent/capabilities")
        assert resp.status_code == 200

    def test_response_is_list_of_capabilities(self):
        with patch(
            "routes.agent_registry_routes.get_agent_registry",
            return_value=_make_registry(),
        ):
            client = TestClient(_build_app())
            data = client.get("/api/agents/research_agent/capabilities").json()
        assert isinstance(data, list)
        assert "web_search" in data

    def test_unknown_agent_returns_404(self):
        with patch(
            "routes.agent_registry_routes.get_agent_registry",
            return_value=_make_registry(),
        ):
            client = TestClient(_build_app())
            resp = client.get("/api/agents/nonexistent_agent/capabilities")
        assert resp.status_code == 404


# ---------------------------------------------------------------------------
# GET /api/agents/by-phase/{phase}
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestGetAgentsByPhase:
    def test_returns_200(self):
        with patch(
            "routes.agent_registry_routes.get_agent_registry",
            return_value=_make_registry(),
        ):
            client = TestClient(_build_app())
            resp = client.get("/api/agents/by-phase/draft")
        assert resp.status_code == 200

    def test_response_is_list_of_agents(self):
        with patch(
            "routes.agent_registry_routes.get_agent_registry",
            return_value=_make_registry(),
        ):
            client = TestClient(_build_app())
            data = client.get("/api/agents/by-phase/draft").json()
        assert isinstance(data, list)

    def test_unknown_phase_returns_empty_list(self):
        reg = _make_registry()
        reg.list_by_phase = MagicMock(return_value=[])
        with patch(
            "routes.agent_registry_routes.get_agent_registry",
            return_value=reg,
        ):
            client = TestClient(_build_app())
            data = client.get("/api/agents/by-phase/nonexistent_phase").json()
        assert data == []


# ---------------------------------------------------------------------------
# GET /api/agents/by-capability/{capability}
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestGetAgentsByCapability:
    def test_returns_200(self):
        with patch(
            "routes.agent_registry_routes.get_agent_registry",
            return_value=_make_registry(),
        ):
            client = TestClient(_build_app())
            resp = client.get("/api/agents/by-capability/web_search")
        assert resp.status_code == 200

    def test_response_is_list_of_agents(self):
        with patch(
            "routes.agent_registry_routes.get_agent_registry",
            return_value=_make_registry(),
        ):
            client = TestClient(_build_app())
            data = client.get("/api/agents/by-capability/web_search").json()
        assert isinstance(data, list)


# ---------------------------------------------------------------------------
# GET /api/agents/by-category/{category}
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestGetAgentsByCategory:
    def test_returns_200_for_known_category(self):
        with patch(
            "routes.agent_registry_routes.get_agent_registry",
            return_value=_make_registry(),
        ):
            client = TestClient(_build_app())
            resp = client.get("/api/agents/by-category/content")
        assert resp.status_code == 200

    def test_response_contains_category_agents(self):
        with patch(
            "routes.agent_registry_routes.get_agent_registry",
            return_value=_make_registry(),
        ):
            client = TestClient(_build_app())
            data = client.get("/api/agents/by-category/content").json()
        names = [a["name"] for a in data]
        assert "research_agent" in names
        assert "creative_agent" in names

    def test_unknown_category_returns_empty_list(self):
        with patch(
            "routes.agent_registry_routes.get_agent_registry",
            return_value=_make_registry(),
        ):
            client = TestClient(_build_app())
            data = client.get("/api/agents/by-category/nonexistent").json()
        assert data == []


# ---------------------------------------------------------------------------
# GET /api/agents/search
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestSearchAgents:
    def test_returns_200_with_no_filters(self):
        with patch(
            "routes.agent_registry_routes.get_agent_registry",
            return_value=_make_registry(),
        ):
            client = TestClient(_build_app())
            resp = client.get("/api/agents/search")
        assert resp.status_code == 200

    def test_returns_all_agents_with_no_filters(self):
        with patch(
            "routes.agent_registry_routes.get_agent_registry",
            return_value=_make_registry(),
        ):
            client = TestClient(_build_app())
            data = client.get("/api/agents/search").json()
        assert len(data) == len(SAMPLE_AGENTS)

    def test_category_filter_narrows_results(self):
        with patch(
            "routes.agent_registry_routes.get_agent_registry",
            return_value=_make_registry(),
        ):
            client = TestClient(_build_app())
            data = client.get("/api/agents/search?category=financial").json()
        assert all(a["category"] == "financial" for a in data)

    def test_phase_filter_narrows_results(self):
        with patch(
            "routes.agent_registry_routes.get_agent_registry",
            return_value=_make_registry(),
        ):
            client = TestClient(_build_app())
            data = client.get("/api/agents/search?phase=research").json()
        assert all("research" in a["phases"] for a in data)

    def test_capability_filter_narrows_results(self):
        with patch(
            "routes.agent_registry_routes.get_agent_registry",
            return_value=_make_registry(),
        ):
            client = TestClient(_build_app())
            data = client.get("/api/agents/search?capability=web_search").json()
        assert all("web_search" in a["capabilities"] for a in data)

    def test_no_match_returns_empty_list(self):
        with patch(
            "routes.agent_registry_routes.get_agent_registry",
            return_value=_make_registry(),
        ):
            client = TestClient(_build_app())
            data = client.get("/api/agents/search?capability=nonexistent_capability").json()
        assert data == []


# ---------------------------------------------------------------------------
# Auth guard — all endpoints require authentication
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestAgentRegistryRoutesRequireAuth:
    """Verify that each endpoint returns 401 when no auth token is provided."""

    def test_registry_requires_auth(self):
        with patch(
            "routes.agent_registry_routes.get_agent_registry", return_value=_make_registry()
        ):
            client = TestClient(_build_app_unauthenticated(), raise_server_exceptions=False)
            resp = client.get("/api/agents/registry")
        assert resp.status_code == 401

    def test_list_requires_auth(self):
        with patch(
            "routes.agent_registry_routes.get_agent_registry", return_value=_make_registry()
        ):
            client = TestClient(_build_app_unauthenticated(), raise_server_exceptions=False)
            resp = client.get("/api/agents/list")
        assert resp.status_code == 401

    def test_search_requires_auth(self):
        with patch(
            "routes.agent_registry_routes.get_agent_registry", return_value=_make_registry()
        ):
            client = TestClient(_build_app_unauthenticated(), raise_server_exceptions=False)
            resp = client.get("/api/agents/search")
        assert resp.status_code == 401

    def test_agent_metadata_requires_auth(self):
        with patch(
            "routes.agent_registry_routes.get_agent_registry", return_value=_make_registry()
        ):
            client = TestClient(_build_app_unauthenticated(), raise_server_exceptions=False)
            resp = client.get("/api/agents/research_agent")
        assert resp.status_code == 401

    def test_phases_requires_auth(self):
        with patch(
            "routes.agent_registry_routes.get_agent_registry", return_value=_make_registry()
        ):
            client = TestClient(_build_app_unauthenticated(), raise_server_exceptions=False)
            resp = client.get("/api/agents/creative_agent/phases")
        assert resp.status_code == 401

    def test_capabilities_requires_auth(self):
        with patch(
            "routes.agent_registry_routes.get_agent_registry", return_value=_make_registry()
        ):
            client = TestClient(_build_app_unauthenticated(), raise_server_exceptions=False)
            resp = client.get("/api/agents/research_agent/capabilities")
        assert resp.status_code == 401

    def test_by_phase_requires_auth(self):
        with patch(
            "routes.agent_registry_routes.get_agent_registry", return_value=_make_registry()
        ):
            client = TestClient(_build_app_unauthenticated(), raise_server_exceptions=False)
            resp = client.get("/api/agents/by-phase/draft")
        assert resp.status_code == 401

    def test_by_capability_requires_auth(self):
        with patch(
            "routes.agent_registry_routes.get_agent_registry", return_value=_make_registry()
        ):
            client = TestClient(_build_app_unauthenticated(), raise_server_exceptions=False)
            resp = client.get("/api/agents/by-capability/web_search")
        assert resp.status_code == 401

    def test_by_category_requires_auth(self):
        with patch(
            "routes.agent_registry_routes.get_agent_registry", return_value=_make_registry()
        ):
            client = TestClient(_build_app_unauthenticated(), raise_server_exceptions=False)
            resp = client.get("/api/agents/by-category/content")
        assert resp.status_code == 401
