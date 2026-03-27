"""
Unit tests for routes/workflow_progress_routes.py.

Tests cover:
- POST /api/workflow-progress/initialize/{execution_id} — initialize_progress
- POST /api/workflow-progress/start/{execution_id}      — start_execution
- POST /api/workflow-progress/phase/start/{execution_id} — start_phase
- POST /api/workflow-progress/phase/complete/{execution_id} — complete_phase
- POST /api/workflow-progress/phase/fail/{execution_id} — fail_phase
- POST /api/workflow-progress/complete/{execution_id}   — mark_complete
- POST /api/workflow-progress/fail/{execution_id}       — mark_failed
- GET  /api/workflow-progress/status/{execution_id}     — get_progress_status
- DELETE /api/workflow-progress/cleanup/{execution_id}  — cleanup_progress

WorkflowProgressService is provided via dependency override.
Auth is router-level via dependencies=[Depends(get_current_user)].
"""

from unittest.mock import MagicMock

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from routes.auth_unified import get_current_user
from routes.workflow_progress_routes import get_workflow_progress_service, router
from tests.unit.routes.conftest import TEST_USER

EXECUTION_ID = "exec-aaaa-1111-2222-3333-444444444444"


def _make_progress_dict(status="running"):
    return {
        "execution_id": EXECUTION_ID,
        "workflow_id": "wf-123",
        "status": status,
        "current_phase": 0,
        "total_phases": 3,
        "phases": [],
        "started_at": "2026-03-12T08:00:00+00:00",
        "completed_at": None,
    }


def _make_progress_obj(status="running"):
    prog = MagicMock()
    prog.to_dict = MagicMock(return_value=_make_progress_dict(status))
    return prog


def _make_progress_service(progress=None):
    svc = MagicMock()
    _prog = progress or _make_progress_obj()
    svc.create_progress = MagicMock(return_value=_prog)
    svc.start_execution = MagicMock(return_value=_prog)
    svc.start_phase = MagicMock(return_value=_prog)
    svc.complete_phase = MagicMock(return_value=_prog)
    svc.fail_phase = MagicMock(return_value=_prog)
    svc.mark_complete = MagicMock(return_value=_make_progress_obj("completed"))
    svc.mark_failed = MagicMock(return_value=_make_progress_obj("failed"))
    svc.get_progress = MagicMock(return_value=_prog)
    svc.cleanup = MagicMock(return_value=None)
    return svc


def _build_app(svc=None) -> FastAPI:
    app = FastAPI()
    app.include_router(router)
    app.dependency_overrides[get_current_user] = lambda: TEST_USER
    app.dependency_overrides[get_workflow_progress_service] = lambda: (
        svc if svc is not None else _make_progress_service()
    )
    return app


# ---------------------------------------------------------------------------
# POST /api/workflow-progress/initialize/{execution_id}
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestInitializeProgress:
    def test_returns_200(self):
        client = TestClient(_build_app())
        resp = client.post(f"/api/workflow-progress/initialize/{EXECUTION_ID}")
        assert resp.status_code == 200

    def test_response_has_success_and_execution_id(self):
        client = TestClient(_build_app())
        data = client.post(f"/api/workflow-progress/initialize/{EXECUTION_ID}").json()
        assert data["success"] is True
        assert data["execution_id"] == EXECUTION_ID
        assert "progress" in data

    def test_optional_params_accepted(self):
        client = TestClient(_build_app())
        resp = client.post(
            f"/api/workflow-progress/initialize/{EXECUTION_ID}"
            "?workflow_id=wf-456&template=blog&total_phases=5"
        )
        assert resp.status_code == 200

    def test_service_error_returns_500(self):
        svc = _make_progress_service()
        svc.create_progress = MagicMock(side_effect=RuntimeError("storage error"))
        client = TestClient(_build_app(svc))
        resp = client.post(f"/api/workflow-progress/initialize/{EXECUTION_ID}")
        assert resp.status_code == 500

    def test_requires_auth(self):
        app = FastAPI()
        app.include_router(router)
        app.dependency_overrides[get_workflow_progress_service] = lambda: _make_progress_service()
        client = TestClient(app, raise_server_exceptions=False)
        resp = client.post(f"/api/workflow-progress/initialize/{EXECUTION_ID}")
        assert resp.status_code == 401


# ---------------------------------------------------------------------------
# POST /api/workflow-progress/start/{execution_id}
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestStartExecution:
    def test_returns_200(self):
        client = TestClient(_build_app())
        resp = client.post(f"/api/workflow-progress/start/{EXECUTION_ID}")
        assert resp.status_code == 200

    def test_response_has_success_and_progress(self):
        client = TestClient(_build_app())
        data = client.post(f"/api/workflow-progress/start/{EXECUTION_ID}").json()
        assert data["success"] is True
        assert "progress" in data

    def test_custom_message_accepted(self):
        client = TestClient(_build_app())
        resp = client.post(f"/api/workflow-progress/start/{EXECUTION_ID}?message=Kicking+off")
        assert resp.status_code == 200

    def test_service_error_returns_500(self):
        svc = _make_progress_service()
        svc.start_execution = MagicMock(side_effect=ValueError("not found"))
        client = TestClient(_build_app(svc))
        resp = client.post(f"/api/workflow-progress/start/{EXECUTION_ID}")
        assert resp.status_code == 500


# ---------------------------------------------------------------------------
# POST /api/workflow-progress/phase/start/{execution_id}
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestStartPhase:
    def test_returns_200(self):
        client = TestClient(_build_app())
        resp = client.post(
            f"/api/workflow-progress/phase/start/{EXECUTION_ID}"
            "?phase_index=0&phase_name=research"
        )
        assert resp.status_code == 200

    def test_response_has_success_and_progress(self):
        client = TestClient(_build_app())
        data = client.post(
            f"/api/workflow-progress/phase/start/{EXECUTION_ID}" "?phase_index=1&phase_name=draft"
        ).json()
        assert data["success"] is True
        assert "progress" in data

    def test_service_error_returns_500(self):
        svc = _make_progress_service()
        svc.start_phase = MagicMock(side_effect=RuntimeError("phase error"))
        client = TestClient(_build_app(svc))
        resp = client.post(
            f"/api/workflow-progress/phase/start/{EXECUTION_ID}"
            "?phase_index=0&phase_name=research"
        )
        assert resp.status_code == 500


# ---------------------------------------------------------------------------
# POST /api/workflow-progress/phase/complete/{execution_id}
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestCompletePhase:
    def test_returns_200(self):
        client = TestClient(_build_app())
        resp = client.post(
            f"/api/workflow-progress/phase/complete/{EXECUTION_ID}" "?phase_name=research"
        )
        assert resp.status_code == 200

    def test_response_has_success_and_progress(self):
        client = TestClient(_build_app())
        data = client.post(
            f"/api/workflow-progress/phase/complete/{EXECUTION_ID}"
            "?phase_name=research&duration_ms=1500.0"
        ).json()
        assert data["success"] is True

    def test_service_error_returns_500(self):
        svc = _make_progress_service()
        svc.complete_phase = MagicMock(side_effect=KeyError("phase not found"))
        client = TestClient(_build_app(svc))
        resp = client.post(
            f"/api/workflow-progress/phase/complete/{EXECUTION_ID}?phase_name=research"
        )
        assert resp.status_code == 500


# ---------------------------------------------------------------------------
# POST /api/workflow-progress/phase/fail/{execution_id}
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestFailPhase:
    def test_returns_200(self):
        client = TestClient(_build_app())
        resp = client.post(
            f"/api/workflow-progress/phase/fail/{EXECUTION_ID}" "?phase_name=research&error=Timeout"
        )
        assert resp.status_code == 200

    def test_response_has_success(self):
        client = TestClient(_build_app())
        data = client.post(
            f"/api/workflow-progress/phase/fail/{EXECUTION_ID}"
            "?phase_name=draft&error=LLM+failure"
        ).json()
        assert data["success"] is True

    def test_service_error_returns_500(self):
        svc = _make_progress_service()
        svc.fail_phase = MagicMock(side_effect=RuntimeError("db error"))
        client = TestClient(_build_app(svc))
        resp = client.post(
            f"/api/workflow-progress/phase/fail/{EXECUTION_ID}" "?phase_name=research&error=boom"
        )
        assert resp.status_code == 500


# ---------------------------------------------------------------------------
# POST /api/workflow-progress/complete/{execution_id}
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestMarkComplete:
    def test_returns_200(self):
        client = TestClient(_build_app())
        resp = client.post(f"/api/workflow-progress/complete/{EXECUTION_ID}")
        assert resp.status_code == 200

    def test_response_has_success_and_progress(self):
        client = TestClient(_build_app())
        data = client.post(f"/api/workflow-progress/complete/{EXECUTION_ID}").json()
        assert data["success"] is True
        assert "progress" in data

    def test_service_error_returns_500(self):
        svc = _make_progress_service()
        svc.mark_complete = MagicMock(side_effect=AttributeError("bad state"))
        client = TestClient(_build_app(svc))
        resp = client.post(f"/api/workflow-progress/complete/{EXECUTION_ID}")
        assert resp.status_code == 500


# ---------------------------------------------------------------------------
# POST /api/workflow-progress/fail/{execution_id}
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestMarkFailed:
    def test_returns_200(self):
        client = TestClient(_build_app())
        resp = client.post(f"/api/workflow-progress/fail/{EXECUTION_ID}?error=LLM+timeout")
        assert resp.status_code == 200

    def test_response_has_success_and_progress(self):
        client = TestClient(_build_app())
        data = client.post(f"/api/workflow-progress/fail/{EXECUTION_ID}?error=crash").json()
        assert data["success"] is True
        assert "progress" in data

    def test_service_error_returns_500(self):
        svc = _make_progress_service()
        svc.mark_failed = MagicMock(side_effect=RuntimeError("storage error"))
        client = TestClient(_build_app(svc))
        resp = client.post(f"/api/workflow-progress/fail/{EXECUTION_ID}?error=crash")
        assert resp.status_code == 500


# ---------------------------------------------------------------------------
# GET /api/workflow-progress/status/{execution_id}
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestGetProgressStatus:
    def test_returns_200_for_existing_execution(self):
        client = TestClient(_build_app())
        resp = client.get(f"/api/workflow-progress/status/{EXECUTION_ID}")
        assert resp.status_code == 200

    def test_response_has_execution_id(self):
        client = TestClient(_build_app())
        data = client.get(f"/api/workflow-progress/status/{EXECUTION_ID}").json()
        assert data["execution_id"] == EXECUTION_ID

    def test_not_found_returns_404(self):
        svc = _make_progress_service()
        svc.get_progress = MagicMock(return_value=None)
        client = TestClient(_build_app(svc))
        resp = client.get("/api/workflow-progress/status/nonexistent-exec")
        assert resp.status_code == 404

    def test_service_error_returns_500(self):
        svc = _make_progress_service()
        svc.get_progress = MagicMock(side_effect=RuntimeError("db error"))
        client = TestClient(_build_app(svc))
        resp = client.get(f"/api/workflow-progress/status/{EXECUTION_ID}")
        assert resp.status_code == 500


# ---------------------------------------------------------------------------
# DELETE /api/workflow-progress/cleanup/{execution_id}
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestCleanupProgress:
    def test_returns_200(self):
        client = TestClient(_build_app())
        resp = client.delete(f"/api/workflow-progress/cleanup/{EXECUTION_ID}")
        assert resp.status_code == 200

    def test_response_has_success_true(self):
        client = TestClient(_build_app())
        data = client.delete(f"/api/workflow-progress/cleanup/{EXECUTION_ID}").json()
        assert data["success"] is True

    def test_service_error_returns_500(self):
        svc = _make_progress_service()
        svc.cleanup = MagicMock(side_effect=RuntimeError("cleanup failed"))
        client = TestClient(_build_app(svc))
        resp = client.delete(f"/api/workflow-progress/cleanup/{EXECUTION_ID}")
        assert resp.status_code == 500
