"""
Unit tests for routes/capability_tasks_routes.py.

Tests cover:
- GET  /api/capabilities                               — list_capabilities
- GET  /api/capabilities/{name}                        — get_capability
- POST /api/tasks/capability/compose-from-natural-language — compose_task_from_natural_language
- POST /api/tasks/capability/compose-and-execute       — compose_and_execute
- POST /api/tasks/capability                           — create_capability_task
- GET  /api/tasks/capability                           — list_capability_tasks
- GET  /api/tasks/capability/{id}                      — get_capability_task
- PUT  /api/tasks/capability/{id}                      — update_capability_task
- DELETE /api/tasks/capability/{id}                    — delete_capability_task
- POST /api/tasks/capability/{id}/execute              — execute_capability_task_endpoint
- GET  /api/tasks/capability/{id}/executions/{exec_id} — get_execution_result
- GET  /api/tasks/capability/{id}/executions           — list_executions

get_registry, get_composer, and CapabilityTasksService are patched.
Auth is provided via dependency override.
"""

from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from middleware.api_token_auth import verify_api_token
from routes.capability_tasks_routes import router
from tests.unit.routes.conftest import TEST_USER, make_mock_db
from utils.route_utils import get_database_dependency

TASK_ID = "task-aaaa-bbbb-cccc-dddd"
EXEC_ID = "exec-1111-2222-3333-4444"
USER_ID = TEST_USER["id"]


def _make_capability_metadata(name="research", cost_tier="balanced"):
    meta = MagicMock()
    meta.name = name
    meta.description = f"Capability: {name}"
    meta.version = "1.0.0"
    meta.tags = [name]
    meta.cost_tier = cost_tier
    meta.timeout_ms = 60000
    return meta


def _make_capability(name="research"):
    """Mock capability object (registry.get() result)."""
    cap = MagicMock()
    cap.input_schema = MagicMock()
    cap.input_schema.parameters = []
    cap.output_schema = MagicMock()
    cap.output_schema.to_dict = MagicMock(
        return_value={"return_type": "dict", "description": "Output", "output_format": "json"}
    )
    return cap


def _make_registry(has_capability=True):
    reg = MagicMock()
    meta = _make_capability_metadata()
    reg.list_capabilities = MagicMock(return_value={"research": meta} if has_capability else {})
    reg.get_metadata = MagicMock(return_value=meta if has_capability else None)
    reg.get = MagicMock(return_value=_make_capability() if has_capability else None)
    return reg


def _make_step(capability_name="research", output_key="result", order=0):
    step = MagicMock()
    step.capability_name = capability_name
    step.inputs = {}
    step.output_key = output_key
    step.order = order
    return step


def _make_task(task_id=TASK_ID, owner_id=USER_ID):
    task = MagicMock()
    task.id = task_id
    task.name = "Test Task"
    task.description = "A test capability task"
    task.steps = [_make_step()]
    task.tags = ["test"]
    task.owner_id = owner_id
    task.created_at = datetime(2026, 3, 12, 8, 0, 0, tzinfo=timezone.utc)
    return task


def _make_execution(exec_id=EXEC_ID, status="completed"):
    exc = MagicMock()
    exc.execution_id = exec_id
    exc.task_id = TASK_ID
    exc.status = status
    exc.step_results = []
    exc.final_outputs = {}
    exc.total_duration_ms = 1500.0
    exc.progress_percent = 100
    exc.error = None
    exc.started_at = datetime(2026, 3, 12, 8, 0, 0, tzinfo=timezone.utc)
    exc.completed_at = datetime(2026, 3, 12, 8, 0, 2, tzinfo=timezone.utc)
    return exc


def _make_task_service(task=None, execution=None, executions=None):
    svc = MagicMock()
    _task = task or _make_task()
    _exec = execution or _make_execution()
    svc.create_task = AsyncMock(return_value=_task)
    svc.list_tasks = AsyncMock(return_value=([_task], 1))
    svc.get_task = AsyncMock(return_value=_task)
    svc.update_task = AsyncMock(return_value=_task)
    svc.delete_task = AsyncMock(return_value=None)
    svc.persist_execution = AsyncMock(return_value=None)
    svc.get_execution = AsyncMock(return_value=_exec)
    svc.list_executions = AsyncMock(return_value=(executions or [_exec], 1))
    return svc


def _make_composer(success=True):
    comp = MagicMock()
    result = MagicMock()
    result.success = success
    result.explanation = "Composed successfully" if success else "Failed"
    result.confidence = 0.9 if success else 0.0
    result.error = None if success else "Composition failed"
    result.execution_id = EXEC_ID if success else None
    result.task_definition = MagicMock() if success else None
    result.suggested_task = None
    if success and result.task_definition:
        result.task_definition.name = "Composed Task"
        result.task_definition.description = "Auto-composed"
        result.task_definition.steps = []
        result.task_definition.tags = []
    comp.compose_from_request = AsyncMock(return_value=result)
    return comp


def _make_db():
    db = make_mock_db()
    db.pool = MagicMock()  # Non-None so _require_pool doesn't raise 503
    return db


def _build_app(task_svc_patch=None, registry=None, composer=None) -> FastAPI:
    app = FastAPI()
    app.include_router(router)
    app.dependency_overrides[verify_api_token] = lambda: "test-token"
    app.dependency_overrides[get_database_dependency] = lambda: _make_db()
    return app


VALID_CREATE_PAYLOAD = {
    "name": "My Task",
    "steps": [
        {"capability_name": "research", "inputs": {}, "output_key": "research_result", "order": 0}
    ],
}

VALID_NL_PAYLOAD = {
    "request": "Write a blog post about AI trends",
    "auto_execute": False,
    "save_task": False,
}


# ---------------------------------------------------------------------------
# GET /api/capabilities
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestListCapabilities:
    def test_returns_200(self):
        reg = _make_registry()
        with patch("routes.capability_tasks_routes.get_registry", return_value=reg):
            client = TestClient(_build_app())
            resp = client.get("/api/capabilities")
        assert resp.status_code == 200

    def test_response_has_capabilities_and_total(self):
        reg = _make_registry()
        with patch("routes.capability_tasks_routes.get_registry", return_value=reg):
            client = TestClient(_build_app())
            data = client.get("/api/capabilities").json()
        assert "capabilities" in data
        assert "total" in data

    def test_tag_filter_accepted(self):
        reg = _make_registry()
        with patch("routes.capability_tasks_routes.get_registry", return_value=reg):
            client = TestClient(_build_app())
            resp = client.get("/api/capabilities?tag=research")
        assert resp.status_code == 200

    def test_empty_registry_returns_empty_list(self):
        reg = _make_registry(has_capability=False)
        with patch("routes.capability_tasks_routes.get_registry", return_value=reg):
            client = TestClient(_build_app())
            data = client.get("/api/capabilities").json()
        assert data["total"] == 0


# ---------------------------------------------------------------------------
# GET /api/capabilities/{name}
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestGetCapability:
    def test_existing_capability_returns_200(self):
        reg = _make_registry()
        with patch("routes.capability_tasks_routes.get_registry", return_value=reg):
            client = TestClient(_build_app())
            resp = client.get("/api/capabilities/research")
        assert resp.status_code == 200

    def test_response_has_name_and_cost_tier(self):
        reg = _make_registry()
        with patch("routes.capability_tasks_routes.get_registry", return_value=reg):
            client = TestClient(_build_app())
            data = client.get("/api/capabilities/research").json()
        assert "name" in data
        assert "cost_tier" in data

    def test_unknown_capability_returns_404(self):
        reg = _make_registry(has_capability=False)
        with patch("routes.capability_tasks_routes.get_registry", return_value=reg):
            client = TestClient(_build_app())
            resp = client.get("/api/capabilities/nonexistent")
        assert resp.status_code == 404


# ---------------------------------------------------------------------------
# POST /api/tasks/capability/compose-from-natural-language
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestComposeFromNaturalLanguage:
    def test_success_returns_200(self):
        comp = _make_composer(success=True)
        with patch("routes.capability_tasks_routes.get_composer", return_value=comp):
            client = TestClient(_build_app())
            resp = client.post(
                "/api/tasks/capability/compose-from-natural-language",
                json=VALID_NL_PAYLOAD,
            )
        assert resp.status_code == 200

    def test_successful_response_has_success_true(self):
        comp = _make_composer(success=True)
        with patch("routes.capability_tasks_routes.get_composer", return_value=comp):
            client = TestClient(_build_app())
            data = client.post(
                "/api/tasks/capability/compose-from-natural-language",
                json=VALID_NL_PAYLOAD,
            ).json()
        assert data["success"] is True
        assert "explanation" in data
        assert "confidence" in data

    def test_failed_composition_returns_200_with_success_false(self):
        comp = _make_composer(success=False)
        with patch("routes.capability_tasks_routes.get_composer", return_value=comp):
            client = TestClient(_build_app())
            data = client.post(
                "/api/tasks/capability/compose-from-natural-language",
                json=VALID_NL_PAYLOAD,
            ).json()
        assert data["success"] is False

    def test_request_too_short_returns_422(self):
        client = TestClient(_build_app())
        resp = client.post(
            "/api/tasks/capability/compose-from-natural-language",
            json={"request": "too short"},  # min_length=10
        )
        assert resp.status_code == 422

    def test_requires_auth(self):
        app = FastAPI()
        app.include_router(router)
        app.dependency_overrides[get_database_dependency] = lambda: _make_db()
        client = TestClient(app, raise_server_exceptions=False)
        resp = client.post(
            "/api/tasks/capability/compose-from-natural-language", json=VALID_NL_PAYLOAD
        )
        assert resp.status_code == 401


# ---------------------------------------------------------------------------
# POST /api/tasks/capability
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestCreateCapabilityTask:
    def test_valid_request_returns_200(self):
        reg = _make_registry()
        svc = _make_task_service()
        with (
            patch("routes.capability_tasks_routes.get_registry", return_value=reg),
            patch("routes.capability_tasks_routes.CapabilityTasksService", return_value=svc),
        ):
            client = TestClient(_build_app())
            resp = client.post("/api/tasks/capability", json=VALID_CREATE_PAYLOAD)
        assert resp.status_code == 200

    def test_response_has_task_fields(self):
        reg = _make_registry()
        svc = _make_task_service()
        with (
            patch("routes.capability_tasks_routes.get_registry", return_value=reg),
            patch("routes.capability_tasks_routes.CapabilityTasksService", return_value=svc),
        ):
            client = TestClient(_build_app())
            data = client.post("/api/tasks/capability", json=VALID_CREATE_PAYLOAD).json()
        for field in ["id", "name", "steps", "owner_id", "created_at"]:
            assert field in data

    def test_unknown_capability_returns_400(self):
        reg = _make_registry(has_capability=False)
        with patch("routes.capability_tasks_routes.get_registry", return_value=reg):
            client = TestClient(_build_app())
            resp = client.post("/api/tasks/capability", json=VALID_CREATE_PAYLOAD)
        assert resp.status_code == 400

    def test_missing_name_returns_422(self):
        client = TestClient(_build_app())
        resp = client.post("/api/tasks/capability", json={"steps": []})
        assert resp.status_code == 422

    def test_db_pool_not_initialized_returns_503(self):
        reg = _make_registry()
        db = make_mock_db()
        db.pool = None  # Pool not initialized
        app = FastAPI()
        app.include_router(router)
        app.dependency_overrides[verify_api_token] = lambda: "test-token"
        app.dependency_overrides[get_database_dependency] = lambda: db
        with patch("routes.capability_tasks_routes.get_registry", return_value=reg):
            client = TestClient(app, raise_server_exceptions=False)
            resp = client.post("/api/tasks/capability", json=VALID_CREATE_PAYLOAD)
        assert resp.status_code == 503


# ---------------------------------------------------------------------------
# GET /api/tasks/capability
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestListCapabilityTasks:
    def test_returns_200(self):
        svc = _make_task_service()
        with patch("routes.capability_tasks_routes.CapabilityTasksService", return_value=svc):
            client = TestClient(_build_app())
            resp = client.get("/api/tasks/capability")
        assert resp.status_code == 200

    def test_response_has_tasks_and_total(self):
        svc = _make_task_service()
        with patch("routes.capability_tasks_routes.CapabilityTasksService", return_value=svc):
            client = TestClient(_build_app())
            data = client.get("/api/tasks/capability").json()
        assert "tasks" in data
        assert "total" in data

    def test_pagination_params_accepted(self):
        svc = _make_task_service()
        with patch("routes.capability_tasks_routes.CapabilityTasksService", return_value=svc):
            client = TestClient(_build_app())
            resp = client.get("/api/tasks/capability?skip=10&limit=25")
        assert resp.status_code == 200


# ---------------------------------------------------------------------------
# GET /api/tasks/capability/{id}
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestGetCapabilityTask:
    def test_existing_task_returns_200(self):
        svc = _make_task_service()
        with patch("routes.capability_tasks_routes.CapabilityTasksService", return_value=svc):
            client = TestClient(_build_app())
            resp = client.get(f"/api/tasks/capability/{TASK_ID}")
        assert resp.status_code == 200

    def test_response_has_task_fields(self):
        svc = _make_task_service()
        with patch("routes.capability_tasks_routes.CapabilityTasksService", return_value=svc):
            client = TestClient(_build_app())
            data = client.get(f"/api/tasks/capability/{TASK_ID}").json()
        assert data["id"] == TASK_ID

    def test_not_found_returns_404(self):
        svc = _make_task_service()
        svc.get_task = AsyncMock(return_value=None)
        with patch("routes.capability_tasks_routes.CapabilityTasksService", return_value=svc):
            client = TestClient(_build_app())
            resp = client.get("/api/tasks/capability/nonexistent")
        assert resp.status_code == 404


# ---------------------------------------------------------------------------
# PUT /api/tasks/capability/{id}
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestUpdateCapabilityTask:
    def test_existing_task_returns_200(self):
        reg = _make_registry()
        svc = _make_task_service()
        with (
            patch("routes.capability_tasks_routes.get_registry", return_value=reg),
            patch("routes.capability_tasks_routes.CapabilityTasksService", return_value=svc),
        ):
            client = TestClient(_build_app())
            resp = client.put(f"/api/tasks/capability/{TASK_ID}", json=VALID_CREATE_PAYLOAD)
        assert resp.status_code == 200

    def test_not_found_returns_404(self):
        reg = _make_registry()
        svc = _make_task_service()
        svc.get_task = AsyncMock(return_value=None)
        with (
            patch("routes.capability_tasks_routes.get_registry", return_value=reg),
            patch("routes.capability_tasks_routes.CapabilityTasksService", return_value=svc),
        ):
            client = TestClient(_build_app())
            resp = client.put("/api/tasks/capability/nonexistent", json=VALID_CREATE_PAYLOAD)
        assert resp.status_code == 404

    def test_unknown_capability_returns_400(self):
        reg = _make_registry(has_capability=False)
        svc = _make_task_service()
        with (
            patch("routes.capability_tasks_routes.get_registry", return_value=reg),
            patch("routes.capability_tasks_routes.CapabilityTasksService", return_value=svc),
        ):
            client = TestClient(_build_app())
            resp = client.put(f"/api/tasks/capability/{TASK_ID}", json=VALID_CREATE_PAYLOAD)
        assert resp.status_code == 400


# ---------------------------------------------------------------------------
# DELETE /api/tasks/capability/{id}
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestDeleteCapabilityTask:
    def test_existing_task_returns_200(self):
        svc = _make_task_service()
        with patch("routes.capability_tasks_routes.CapabilityTasksService", return_value=svc):
            client = TestClient(_build_app())
            resp = client.delete(f"/api/tasks/capability/{TASK_ID}")
        assert resp.status_code == 200

    def test_response_has_message(self):
        svc = _make_task_service()
        with patch("routes.capability_tasks_routes.CapabilityTasksService", return_value=svc):
            client = TestClient(_build_app())
            data = client.delete(f"/api/tasks/capability/{TASK_ID}").json()
        assert "message" in data

    def test_not_found_returns_404(self):
        svc = _make_task_service()
        svc.get_task = AsyncMock(return_value=None)
        with patch("routes.capability_tasks_routes.CapabilityTasksService", return_value=svc):
            client = TestClient(_build_app())
            resp = client.delete("/api/tasks/capability/nonexistent")
        assert resp.status_code == 404


# ---------------------------------------------------------------------------
# POST /api/tasks/capability/{id}/execute
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestExecuteCapabilityTask:
    def test_returns_200(self):
        svc = _make_task_service()
        exec_result = _make_execution()
        with (
            patch("routes.capability_tasks_routes.CapabilityTasksService", return_value=svc),
            patch(
                "routes.capability_tasks_routes.execute_capability_task",
                new=AsyncMock(return_value=exec_result),
            ),
        ):
            client = TestClient(_build_app())
            resp = client.post(f"/api/tasks/capability/{TASK_ID}/execute")
        assert resp.status_code == 200

    def test_response_has_execution_fields(self):
        svc = _make_task_service()
        exec_result = _make_execution()
        with (
            patch("routes.capability_tasks_routes.CapabilityTasksService", return_value=svc),
            patch(
                "routes.capability_tasks_routes.execute_capability_task",
                new=AsyncMock(return_value=exec_result),
            ),
        ):
            client = TestClient(_build_app())
            data = client.post(f"/api/tasks/capability/{TASK_ID}/execute").json()
        for field in ["execution_id", "task_id", "status", "step_results"]:
            assert field in data

    def test_task_not_found_returns_404(self):
        svc = _make_task_service()
        svc.get_task = AsyncMock(return_value=None)
        with patch("routes.capability_tasks_routes.CapabilityTasksService", return_value=svc):
            client = TestClient(_build_app())
            resp = client.post("/api/tasks/capability/nonexistent/execute")
        assert resp.status_code == 404

    def test_execution_error_returns_200_with_failed_status(self):
        """Execution errors are caught and returned as failed ExecutionResponse (200)."""
        svc = _make_task_service()
        with (
            patch("routes.capability_tasks_routes.CapabilityTasksService", return_value=svc),
            patch(
                "routes.capability_tasks_routes.execute_capability_task",
                new=AsyncMock(side_effect=RuntimeError("Executor failed")),
            ),
        ):
            client = TestClient(_build_app())
            data = client.post(f"/api/tasks/capability/{TASK_ID}/execute").json()
        assert data["status"] == "failed"


# ---------------------------------------------------------------------------
# GET /api/tasks/capability/{id}/executions/{exec_id}
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestGetExecutionResult:
    def test_returns_200_for_existing_execution(self):
        svc = _make_task_service()
        with patch("routes.capability_tasks_routes.CapabilityTasksService", return_value=svc):
            client = TestClient(_build_app())
            resp = client.get(f"/api/tasks/capability/{TASK_ID}/executions/{EXEC_ID}")
        assert resp.status_code == 200

    def test_response_has_execution_id(self):
        svc = _make_task_service()
        with patch("routes.capability_tasks_routes.CapabilityTasksService", return_value=svc):
            client = TestClient(_build_app())
            data = client.get(f"/api/tasks/capability/{TASK_ID}/executions/{EXEC_ID}").json()
        assert data["execution_id"] == EXEC_ID

    def test_not_found_returns_404(self):
        svc = _make_task_service()
        svc.get_execution = AsyncMock(return_value=None)
        with patch("routes.capability_tasks_routes.CapabilityTasksService", return_value=svc):
            client = TestClient(_build_app())
            resp = client.get(f"/api/tasks/capability/{TASK_ID}/executions/nonexistent")
        assert resp.status_code == 404


# ---------------------------------------------------------------------------
# GET /api/tasks/capability/{id}/executions
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestListExecutions:
    def test_returns_200(self):
        svc = _make_task_service()
        with patch("routes.capability_tasks_routes.CapabilityTasksService", return_value=svc):
            client = TestClient(_build_app())
            resp = client.get(f"/api/tasks/capability/{TASK_ID}/executions")
        assert resp.status_code == 200

    def test_response_has_executions_and_total(self):
        svc = _make_task_service()
        with patch("routes.capability_tasks_routes.CapabilityTasksService", return_value=svc):
            client = TestClient(_build_app())
            data = client.get(f"/api/tasks/capability/{TASK_ID}/executions").json()
        assert "executions" in data
        assert "total" in data

    def test_task_not_found_returns_404(self):
        svc = _make_task_service()
        svc.get_task = AsyncMock(return_value=None)
        with patch("routes.capability_tasks_routes.CapabilityTasksService", return_value=svc):
            client = TestClient(_build_app())
            resp = client.get("/api/tasks/capability/nonexistent/executions")
        assert resp.status_code == 404

    def test_status_filter_accepted(self):
        svc = _make_task_service()
        with patch("routes.capability_tasks_routes.CapabilityTasksService", return_value=svc):
            client = TestClient(_build_app())
            resp = client.get(f"/api/tasks/capability/{TASK_ID}/executions?status=completed")
        assert resp.status_code == 200
