"""
Unit tests for routes/custom_workflows_routes.py.

Tests cover:
- POST /api/workflows/custom                    — create_custom_workflow
- GET  /api/workflows/custom                    — list_custom_workflows
- GET  /api/workflows/custom/{id}               — get_custom_workflow
- PUT  /api/workflows/custom/{id}               — update_custom_workflow
- DELETE /api/workflows/custom/{id}             — delete_custom_workflow
- GET  /api/workflows/executions/{id}           — get_workflow_execution_status
- GET  /api/workflows/custom-executions         — list_custom_workflow_executions
- GET  /api/workflows/available-phases          — get_available_phases

CustomWorkflowsService is provided via dependency override.
get_workflows_service reads from app.state — we override the dependency directly.
"""

import pytest
from datetime import datetime, timezone
from fastapi import FastAPI
from fastapi.testclient import TestClient
from unittest.mock import AsyncMock, MagicMock

from routes.auth_unified import get_current_user
from routes.custom_workflows_routes import router, get_workflows_service
from tests.unit.routes.conftest import TEST_USER
from schemas.custom_workflow_schemas import (
    AvailablePhase,
    AvailablePhasesResponse,
    CustomWorkflow,
    WorkflowListPageResponse,
    WorkflowListResponse,
)


WORKFLOW_ID = "wf-11111111-1111-1111-1111-111111111111"
EXECUTION_ID = "exec-22222222-2222-2222-2222-222222222222"

SAMPLE_WORKFLOW = CustomWorkflow(  # type: ignore[call-arg]
    id=WORKFLOW_ID,
    name="Test Blog Pipeline",
    description="A test workflow for blog content generation",
    phases=[{"name": "research", "agent": "content_agent"}],
    owner_id="test-user-123",
    created_at=datetime(2026, 3, 1, tzinfo=timezone.utc),
    updated_at=datetime(2026, 3, 12, tzinfo=timezone.utc),
    tags=["blog", "content"],
    is_template=False,
)

SAMPLE_EXECUTION = {
    "id": EXECUTION_ID,
    "workflow_id": WORKFLOW_ID,
    "execution_status": "completed",
    "started_at": "2026-03-12T10:00:00+00:00",
    "completed_at": "2026-03-12T10:05:00+00:00",
    "duration_ms": 300000,
    "progress_percent": 100,
    "completed_phases": 1,
    "total_phases": 1,
    "phase_results": {},
    "final_output": {"content": "Generated blog post"},
    "error_message": None,
    "metadata": {"current_phase": None, "last_updated_at": None},
}

SAMPLE_PHASE = AvailablePhase(
    name="research",
    description="Researches topic using web search",
    category="content",
    default_timeout_seconds=60,
    compatible_agents=["content_agent"],
    capabilities=["web_search"],
    default_retries=3,
    supports_model_selection=True,
    input_fields=[],
    version="1.0",
)


def _make_svc(
    workflow=None,
    workflows_list=None,
    execution=None,
    executions_list=None,
    phases=None,
    delete_success=True,
):
    svc = MagicMock()
    svc.create_workflow = AsyncMock(return_value=workflow or SAMPLE_WORKFLOW)
    svc.list_workflows = AsyncMock(
        return_value=workflows_list
        or {
            "workflows": [SAMPLE_WORKFLOW],
            "total_count": 1,
            "has_next": False,
        }
    )
    svc.get_workflow = AsyncMock(return_value=workflow or SAMPLE_WORKFLOW)
    svc.update_workflow = AsyncMock(return_value=workflow or SAMPLE_WORKFLOW)
    svc.delete_workflow = AsyncMock(return_value=delete_success)
    svc.get_workflow_execution = AsyncMock(return_value=execution or SAMPLE_EXECUTION)
    svc.persist_workflow_execution = AsyncMock(return_value=True)
    svc.get_workflow_executions = AsyncMock(
        return_value=executions_list
        or {
            "executions": [SAMPLE_EXECUTION],
            "total": 1,
            "limit": 50,
            "offset": 0,
        }
    )
    svc.get_available_phases = AsyncMock(return_value=phases or [SAMPLE_PHASE])
    return svc


def _build_app(svc=None) -> FastAPI:
    if svc is None:
        svc = _make_svc()
    app = FastAPI()
    app.include_router(router)
    # Override the service and auth dependencies
    app.dependency_overrides[get_workflows_service] = lambda: svc
    app.dependency_overrides[get_current_user] = lambda: TEST_USER
    return app


VALID_WORKFLOW_PAYLOAD = {
    "name": "My Blog Pipeline",
    "description": "Generates blog posts with research and writing phases",
    "phases": [{"name": "research", "agent": "content_agent"}],
    "tags": ["blog"],
    "is_template": False,
}


# ---------------------------------------------------------------------------
# POST /api/workflows/custom
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestCreateCustomWorkflow:
    def test_returns_200(self):
        client = TestClient(_build_app())
        resp = client.post("/api/workflows/custom", json=VALID_WORKFLOW_PAYLOAD)
        assert resp.status_code == 200

    def test_response_has_workflow_id(self):
        client = TestClient(_build_app())
        data = client.post("/api/workflows/custom", json=VALID_WORKFLOW_PAYLOAD).json()
        assert "id" in data
        assert data["name"] == "Test Blog Pipeline"

    def test_missing_name_returns_422(self):
        client = TestClient(_build_app())
        payload = {**VALID_WORKFLOW_PAYLOAD}
        del payload["name"]
        resp = client.post("/api/workflows/custom", json=payload)
        assert resp.status_code == 422

    def test_empty_phases_returns_422(self):
        client = TestClient(_build_app())
        payload = {**VALID_WORKFLOW_PAYLOAD, "phases": []}
        resp = client.post("/api/workflows/custom", json=payload)
        assert resp.status_code == 422

    def test_service_error_returns_500(self):
        svc = _make_svc()
        svc.create_workflow = AsyncMock(side_effect=RuntimeError("DB error"))
        client = TestClient(_build_app(svc))
        resp = client.post("/api/workflows/custom", json=VALID_WORKFLOW_PAYLOAD)
        assert resp.status_code == 500

    def test_value_error_returns_400(self):
        svc = _make_svc()
        svc.create_workflow = AsyncMock(side_effect=ValueError("Invalid workflow"))
        client = TestClient(_build_app(svc))
        resp = client.post("/api/workflows/custom", json=VALID_WORKFLOW_PAYLOAD)
        assert resp.status_code == 400


# ---------------------------------------------------------------------------
# GET /api/workflows/custom
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestListCustomWorkflows:
    def test_returns_200(self):
        client = TestClient(_build_app())
        resp = client.get("/api/workflows/custom")
        assert resp.status_code == 200

    def test_response_has_workflows_and_total(self):
        client = TestClient(_build_app())
        data = client.get("/api/workflows/custom").json()
        assert "workflows" in data
        assert "total_count" in data

    def test_empty_list_returns_200(self):
        svc = _make_svc(
            workflows_list={"workflows": [], "total_count": 0, "has_next": False}
        )
        client = TestClient(_build_app(svc))
        data = client.get("/api/workflows/custom").json()
        assert data["total_count"] == 0

    def test_pagination_params_accepted(self):
        client = TestClient(_build_app())
        resp = client.get("/api/workflows/custom?page=2&page_size=10")
        assert resp.status_code == 200

    def test_service_error_returns_500(self):
        svc = _make_svc()
        svc.list_workflows = AsyncMock(side_effect=RuntimeError("DB error"))
        client = TestClient(_build_app(svc))
        resp = client.get("/api/workflows/custom")
        assert resp.status_code == 500


# ---------------------------------------------------------------------------
# GET /api/workflows/custom/{workflow_id}
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestGetCustomWorkflow:
    def test_returns_200_for_existing_workflow(self):
        client = TestClient(_build_app())
        resp = client.get(f"/api/workflows/custom/{WORKFLOW_ID}")
        assert resp.status_code == 200

    def test_response_has_name_and_phases(self):
        client = TestClient(_build_app())
        data = client.get(f"/api/workflows/custom/{WORKFLOW_ID}").json()
        assert "name" in data
        assert "phases" in data

    def test_not_found_returns_404(self):
        svc = _make_svc()
        svc.get_workflow = AsyncMock(return_value=None)
        client = TestClient(_build_app(svc))
        resp = client.get("/api/workflows/custom/nonexistent-id")
        assert resp.status_code == 404

    def test_service_error_returns_500(self):
        svc = _make_svc()
        svc.get_workflow = AsyncMock(side_effect=RuntimeError("DB error"))
        client = TestClient(_build_app(svc))
        resp = client.get(f"/api/workflows/custom/{WORKFLOW_ID}")
        assert resp.status_code == 500


# ---------------------------------------------------------------------------
# PUT /api/workflows/custom/{workflow_id}
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestUpdateCustomWorkflow:
    def test_returns_200(self):
        client = TestClient(_build_app())
        resp = client.put(
            f"/api/workflows/custom/{WORKFLOW_ID}", json=VALID_WORKFLOW_PAYLOAD
        )
        assert resp.status_code == 200

    def test_value_error_not_found_returns_404(self):
        svc = _make_svc()
        svc.update_workflow = AsyncMock(side_effect=ValueError("not found"))
        client = TestClient(_build_app(svc))
        resp = client.put(
            f"/api/workflows/custom/{WORKFLOW_ID}", json=VALID_WORKFLOW_PAYLOAD
        )
        assert resp.status_code == 404

    def test_value_error_invalid_returns_400(self):
        svc = _make_svc()
        svc.update_workflow = AsyncMock(side_effect=ValueError("Invalid phase config"))
        client = TestClient(_build_app(svc))
        resp = client.put(
            f"/api/workflows/custom/{WORKFLOW_ID}", json=VALID_WORKFLOW_PAYLOAD
        )
        assert resp.status_code == 400

    def test_service_error_returns_500(self):
        svc = _make_svc()
        svc.update_workflow = AsyncMock(side_effect=RuntimeError("DB error"))
        client = TestClient(_build_app(svc))
        resp = client.put(
            f"/api/workflows/custom/{WORKFLOW_ID}", json=VALID_WORKFLOW_PAYLOAD
        )
        assert resp.status_code == 500


# ---------------------------------------------------------------------------
# DELETE /api/workflows/custom/{workflow_id}
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestDeleteCustomWorkflow:
    def test_returns_200_on_success(self):
        client = TestClient(_build_app())
        resp = client.delete(f"/api/workflows/custom/{WORKFLOW_ID}")
        assert resp.status_code == 200

    def test_response_has_message(self):
        client = TestClient(_build_app())
        data = client.delete(f"/api/workflows/custom/{WORKFLOW_ID}").json()
        assert "message" in data

    def test_not_found_returns_404(self):
        svc = _make_svc(delete_success=False)
        client = TestClient(_build_app(svc))
        resp = client.delete("/api/workflows/custom/nonexistent-id")
        assert resp.status_code == 404

    def test_service_error_returns_500(self):
        svc = _make_svc()
        svc.delete_workflow = AsyncMock(side_effect=RuntimeError("DB error"))
        client = TestClient(_build_app(svc))
        resp = client.delete(f"/api/workflows/custom/{WORKFLOW_ID}")
        assert resp.status_code == 500


# ---------------------------------------------------------------------------
# GET /api/workflows/executions/{execution_id}
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestGetWorkflowExecutionStatus:
    def test_returns_200_for_existing_execution(self):
        client = TestClient(_build_app())
        resp = client.get(f"/api/workflows/executions/{EXECUTION_ID}")
        assert resp.status_code == 200

    def test_response_has_required_fields(self):
        client = TestClient(_build_app())
        data = client.get(f"/api/workflows/executions/{EXECUTION_ID}").json()
        for field in ["execution_id", "workflow_id", "status", "progress_percent"]:
            assert field in data

    def test_not_found_returns_404(self):
        svc = _make_svc()
        svc.get_workflow_execution = AsyncMock(return_value=None)
        client = TestClient(_build_app(svc))
        resp = client.get("/api/workflows/executions/nonexistent-id")
        assert resp.status_code == 404

    def test_service_error_returns_500(self):
        svc = _make_svc()
        svc.get_workflow_execution = AsyncMock(side_effect=RuntimeError("DB error"))
        client = TestClient(_build_app(svc))
        resp = client.get(f"/api/workflows/executions/{EXECUTION_ID}")
        assert resp.status_code == 500


# ---------------------------------------------------------------------------
# GET /api/workflows/custom-executions
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestListWorkflowExecutions:
    def test_returns_200(self):
        client = TestClient(_build_app())
        resp = client.get(f"/api/workflows/custom-executions?workflow_id={WORKFLOW_ID}")
        assert resp.status_code == 200

    def test_response_has_executions_list(self):
        client = TestClient(_build_app())
        data = client.get(f"/api/workflows/custom-executions?workflow_id={WORKFLOW_ID}").json()
        assert "executions" in data
        assert "total" in data

    def test_empty_executions_returns_200(self):
        svc = _make_svc(
            executions_list={"executions": [], "total": 0, "limit": 50, "offset": 0}
        )
        client = TestClient(_build_app(svc))
        data = client.get(f"/api/workflows/custom-executions?workflow_id={WORKFLOW_ID}").json()
        assert data["total"] == 0

    def test_service_error_returns_500(self):
        svc = _make_svc()
        svc.get_workflow_executions = AsyncMock(side_effect=RuntimeError("DB error"))
        client = TestClient(_build_app(svc))
        resp = client.get(f"/api/workflows/custom-executions?workflow_id={WORKFLOW_ID}")
        assert resp.status_code == 500


# ---------------------------------------------------------------------------
# GET /api/workflows/available-phases
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestGetAvailablePhases:
    def test_returns_200(self):
        client = TestClient(_build_app())
        resp = client.get("/api/workflows/available-phases")
        assert resp.status_code == 200

    def test_response_has_phases_and_total_count(self):
        client = TestClient(_build_app())
        data = client.get("/api/workflows/available-phases").json()
        assert "phases" in data
        assert "total_count" in data
        assert data["total_count"] == 1

    def test_each_phase_has_required_fields(self):
        client = TestClient(_build_app())
        data = client.get("/api/workflows/available-phases").json()
        if data["phases"]:
            phase = data["phases"][0]
            for field in ["name", "description", "category", "compatible_agents"]:
                assert field in phase

    def test_service_error_returns_500(self):
        svc = _make_svc()
        svc.get_available_phases = AsyncMock(side_effect=RuntimeError("registry error"))
        client = TestClient(_build_app(svc))
        resp = client.get("/api/workflows/available-phases")
        assert resp.status_code == 500
