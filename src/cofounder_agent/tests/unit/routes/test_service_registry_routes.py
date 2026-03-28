"""
Unit tests for routes/service_registry_routes.py.

Tests cover:
- GET  /api/services/registry                        — get_registry_schema
- GET  /api/services/list                            — list_services
- GET  /api/services/{service_name}                  — get_service_metadata
- GET  /api/services/{service_name}/actions          — get_service_actions
- GET  /api/services/{service_name}/actions/{action} — get_action_details
- POST /api/services/{service_name}/actions/{action} — execute_service_action

get_service_registry is patched to avoid real registry I/O.
All endpoints require authentication via get_current_user (overridden with TEST_USER).
"""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from middleware.api_token_auth import verify_api_token
from routes.service_registry_routes import router
from tests.unit.routes.conftest import TEST_USER, make_mock_db
from utils.route_utils import get_database_dependency

SAMPLE_SCHEMA = {
    "content_service": {
        "name": "content_service",
        "description": "Generates blog content",
        "actions": [
            {
                "name": "generate_blog_post",
                "description": "Generate a blog post",
                "parameters": {"type": "object", "properties": {"topic": {"type": "string"}}},
                "response": {"type": "object"},
            }
        ],
    }
}

SAMPLE_ACTIONS = [
    {
        "name": "generate_blog_post",
        "description": "Generate a blog post",
        "parameters": {"type": "object"},
        "response": {"type": "object"},
    }
]


def _make_registry(
    schema=None,
    services=None,
    service_obj=None,
    actions=None,
    execute_result=None,
):
    reg = MagicMock()
    reg.get_registry_schema = MagicMock(return_value=schema or SAMPLE_SCHEMA)
    reg.list_services = MagicMock(return_value=services or ["content_service"])
    reg.get_service = MagicMock(return_value=service_obj or MagicMock())
    reg.list_actions = MagicMock(return_value=actions if actions is not None else SAMPLE_ACTIONS)
    reg.execute_action = AsyncMock(return_value=execute_result or {"content": "Generated content"})
    return reg


def _build_app(db=None) -> FastAPI:
    app = FastAPI()
    app.include_router(router)
    app.dependency_overrides[get_database_dependency] = lambda: (db or make_mock_db())
    app.dependency_overrides[verify_api_token] = lambda: "test-token"
    return app


# ---------------------------------------------------------------------------
# GET /api/services/registry
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestGetRegistrySchema:
    def test_returns_200(self):
        with patch(
            "routes.service_registry_routes.get_service_registry",
            return_value=_make_registry(),
        ):
            client = TestClient(_build_app())
            resp = client.get("/api/services/registry")
        assert resp.status_code == 200

    def test_response_has_services(self):
        with patch(
            "routes.service_registry_routes.get_service_registry",
            return_value=_make_registry(),
        ):
            client = TestClient(_build_app())
            data = client.get("/api/services/registry").json()
        assert "services" in data

    def test_service_error_returns_500(self):
        with patch(
            "routes.service_registry_routes.get_service_registry",
            side_effect=RuntimeError("registry broken"),
        ):
            client = TestClient(_build_app(), raise_server_exceptions=False)
            resp = client.get("/api/services/registry")
        assert resp.status_code == 500


# ---------------------------------------------------------------------------
# GET /api/services/list
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestListServices:
    def test_returns_200(self):
        with patch(
            "routes.service_registry_routes.get_service_registry",
            return_value=_make_registry(),
        ):
            client = TestClient(_build_app())
            resp = client.get("/api/services/list")
        assert resp.status_code == 200

    def test_response_is_list_of_service_names(self):
        with patch(
            "routes.service_registry_routes.get_service_registry",
            return_value=_make_registry(),
        ):
            client = TestClient(_build_app())
            data = client.get("/api/services/list").json()
        assert isinstance(data, list)
        assert "content_service" in data

    def test_service_error_returns_500(self):
        with patch(
            "routes.service_registry_routes.get_service_registry",
            side_effect=RuntimeError("registry broken"),
        ):
            client = TestClient(_build_app(), raise_server_exceptions=False)
            resp = client.get("/api/services/list")
        assert resp.status_code == 500


# ---------------------------------------------------------------------------
# GET /api/services/{service_name}
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestGetServiceMetadata:
    def test_existing_service_returns_200(self):
        with patch(
            "routes.service_registry_routes.get_service_registry",
            return_value=_make_registry(),
        ):
            client = TestClient(_build_app())
            resp = client.get("/api/services/content_service")
        assert resp.status_code == 200

    def test_unknown_service_returns_404(self):
        reg = _make_registry()
        reg.list_services = MagicMock(return_value=["content_service"])
        with patch(
            "routes.service_registry_routes.get_service_registry",
            return_value=reg,
        ):
            client = TestClient(_build_app())
            resp = client.get("/api/services/nonexistent_service")
        assert resp.status_code == 404

    def test_service_error_returns_500(self):
        with patch(
            "routes.service_registry_routes.get_service_registry",
            side_effect=RuntimeError("registry broken"),
        ):
            client = TestClient(_build_app(), raise_server_exceptions=False)
            resp = client.get("/api/services/content_service")
        assert resp.status_code == 500


# ---------------------------------------------------------------------------
# GET /api/services/{service_name}/actions
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestGetServiceActions:
    def test_existing_service_returns_200(self):
        with patch(
            "routes.service_registry_routes.get_service_registry",
            return_value=_make_registry(),
        ):
            client = TestClient(_build_app())
            resp = client.get("/api/services/content_service/actions")
        assert resp.status_code == 200

    def test_response_is_list_of_actions(self):
        with patch(
            "routes.service_registry_routes.get_service_registry",
            return_value=_make_registry(),
        ):
            client = TestClient(_build_app())
            data = client.get("/api/services/content_service/actions").json()
        assert isinstance(data, list)
        assert len(data) > 0
        assert "name" in data[0]

    def test_unknown_service_returns_404(self):
        reg = _make_registry()
        reg.list_actions = MagicMock(return_value=None)
        with patch(
            "routes.service_registry_routes.get_service_registry",
            return_value=reg,
        ):
            client = TestClient(_build_app())
            resp = client.get("/api/services/nonexistent/actions")
        assert resp.status_code == 404


# ---------------------------------------------------------------------------
# GET /api/services/{service_name}/actions/{action_name}
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestGetActionDetails:
    def test_existing_action_returns_200(self):
        with patch(
            "routes.service_registry_routes.get_service_registry",
            return_value=_make_registry(),
        ):
            client = TestClient(_build_app())
            resp = client.get("/api/services/content_service/actions/generate_blog_post")
        assert resp.status_code == 200

    def test_response_has_action_fields(self):
        with patch(
            "routes.service_registry_routes.get_service_registry",
            return_value=_make_registry(),
        ):
            client = TestClient(_build_app())
            data = client.get("/api/services/content_service/actions/generate_blog_post").json()
        assert "name" in data

    def test_unknown_action_returns_404(self):
        with patch(
            "routes.service_registry_routes.get_service_registry",
            return_value=_make_registry(),
        ):
            client = TestClient(_build_app())
            resp = client.get("/api/services/content_service/actions/nonexistent_action")
        assert resp.status_code == 404

    def test_unknown_service_returns_404(self):
        reg = _make_registry()
        reg.list_actions = MagicMock(return_value=None)
        with patch(
            "routes.service_registry_routes.get_service_registry",
            return_value=reg,
        ):
            client = TestClient(_build_app())
            resp = client.get("/api/services/nonexistent/actions/some_action")
        assert resp.status_code == 404


# ---------------------------------------------------------------------------
# POST /api/services/{service_name}/actions/{action_name}
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestExecuteServiceAction:
    def test_valid_action_returns_200(self):
        with patch(
            "routes.service_registry_routes.get_service_registry",
            return_value=_make_registry(),
        ):
            client = TestClient(_build_app())
            resp = client.post(
                "/api/services/content_service/actions/generate_blog_post",
                json={"topic": "AI trends"},
            )
        assert resp.status_code == 200

    def test_response_has_status_success(self):
        with patch(
            "routes.service_registry_routes.get_service_registry",
            return_value=_make_registry(),
        ):
            client = TestClient(_build_app())
            data = client.post(
                "/api/services/content_service/actions/generate_blog_post",
                json={"topic": "AI trends"},
            ).json()
        assert data["status"] == "success"
        assert "result" in data

    def test_unknown_service_returns_404(self):
        reg = _make_registry()
        reg.list_services = MagicMock(return_value=["content_service"])
        with patch(
            "routes.service_registry_routes.get_service_registry",
            return_value=reg,
        ):
            client = TestClient(_build_app())
            resp = client.post(
                "/api/services/nonexistent/actions/some_action",
                json={},
            )
        assert resp.status_code == 404

    def test_unknown_action_returns_404(self):
        reg = _make_registry()
        with patch(
            "routes.service_registry_routes.get_service_registry",
            return_value=reg,
        ):
            client = TestClient(_build_app())
            resp = client.post(
                "/api/services/content_service/actions/nonexistent_action",
                json={},
            )
        assert resp.status_code == 404

    def test_value_error_returns_400(self):
        reg = _make_registry()
        reg.execute_action = AsyncMock(side_effect=ValueError("Invalid param"))
        with patch(
            "routes.service_registry_routes.get_service_registry",
            return_value=reg,
        ):
            client = TestClient(_build_app())
            resp = client.post(
                "/api/services/content_service/actions/generate_blog_post",
                json={},
            )
        assert resp.status_code == 400

    def test_runtime_error_returns_500(self):
        reg = _make_registry()
        reg.execute_action = AsyncMock(side_effect=RuntimeError("execution failed"))
        with patch(
            "routes.service_registry_routes.get_service_registry",
            return_value=reg,
        ):
            client = TestClient(_build_app(), raise_server_exceptions=False)
            resp = client.post(
                "/api/services/content_service/actions/generate_blog_post",
                json={},
            )
        assert resp.status_code == 500
