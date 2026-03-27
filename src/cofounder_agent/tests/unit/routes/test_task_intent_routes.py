"""
Unit tests for routes/task_intent_routes.py.

Tests cover:
- POST /api/tasks/intent         — create_task_from_intent (success, empty input, service failure)
- POST /api/tasks/confirm-intent — confirm_and_execute_task (success, unconfirmed, missing plan, DB failure)
- Auth: unauthenticated requests rejected for both endpoints

Auth and DB are overridden via FastAPI dependency_overrides so no real I/O occurs.
"""

from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from routes.auth_unified import get_current_user
from routes.task_intent_routes import intent_router
from tests.unit.routes.conftest import TEST_USER, make_mock_db
from utils.route_utils import get_database_dependency

# ---------------------------------------------------------------------------
# Stub dataclasses matching the service layer types
# ---------------------------------------------------------------------------


@dataclass
class _StubIntentRequest:
    raw_input: str = "Write a blog post about AI"
    intent_type: str = "content_generation"
    task_type: str = "blog_post"
    confidence: float = 0.92
    parameters: Dict[str, Any] = field(default_factory=lambda: {"topic": "AI trends"})
    suggested_subtasks: List[str] = field(default_factory=lambda: ["research", "creative", "qa"])
    requires_confirmation: bool = True
    execution_strategy: str = "sequential"


@dataclass
class _StubExecutionPlanStage:
    stage_number: int = 1
    stage_name: str = "Research"
    description: str = "Research the topic"
    required_inputs: List[str] = field(default_factory=list)
    estimated_duration_ms: int = 15000
    estimated_cost: float = 0.10
    model: str = "budget"


@dataclass
class _StubExecutionPlan:
    task_id: str = "plan-id-001"
    task_type: str = "blog_post"
    total_estimated_duration_ms: int = 45000
    total_estimated_cost: float = 1.25
    total_estimated_tokens: int = 5000
    stages: List[_StubExecutionPlanStage] = field(
        default_factory=lambda: [_StubExecutionPlanStage()]
    )
    parallelization_strategy: str = "sequential"
    resource_requirements: Dict[str, Any] = field(default_factory=dict)
    estimated_quality_score: float = 85.0
    success_probability: float = 0.9
    alternative_strategies: Optional[list] = None
    created_at: Optional[str] = None
    user_confirmed: bool = False


@dataclass
class _StubPlanSummary:
    title: str = "Blog Post: AI trends"
    description: str = "Generate a blog post about AI trends"
    estimated_time: str = "45 seconds"
    estimated_cost: str = "$1.25"
    confidence: str = "High"
    key_stages: List[str] = field(default_factory=lambda: ["Research", "Creative", "QA"])
    warnings: Optional[List[str]] = None
    opportunities: Optional[List[str]] = None


# ---------------------------------------------------------------------------
# App / client factory helpers
# ---------------------------------------------------------------------------

# Mount the intent_router under /api/tasks so paths match the real app
_PREFIX = "/api/tasks"


def _build_app(mock_db=None, authenticated=True) -> FastAPI:
    """Build a minimal FastAPI app with the intent router and overridden deps."""
    if mock_db is None:
        mock_db = make_mock_db()

    app = FastAPI()
    # The intent_router is a sub-router of task_routes with prefix /api/tasks
    app.include_router(intent_router, prefix=_PREFIX)

    if authenticated:
        app.dependency_overrides[get_current_user] = lambda: TEST_USER

    app.dependency_overrides[get_database_dependency] = lambda: mock_db

    return app


def _make_client(mock_db=None, authenticated=True) -> TestClient:
    return TestClient(_build_app(mock_db=mock_db, authenticated=authenticated))


# ---------------------------------------------------------------------------
# Shared mock builders
# ---------------------------------------------------------------------------


def _mock_intent_router_class():
    """Return a mock TaskIntentRouter class whose instances have route_user_input."""
    mock_cls = MagicMock()
    instance = mock_cls.return_value
    instance.route_user_input = AsyncMock(return_value=_StubIntentRequest())
    return mock_cls


def _mock_planning_service_class():
    """Return a mock TaskPlanningService class whose instances have generate_plan / plan_to_summary / serialize_plan."""
    mock_cls = MagicMock()
    instance = mock_cls.return_value
    instance.generate_plan = AsyncMock(return_value=_StubExecutionPlan())
    instance.plan_to_summary = MagicMock(return_value=_StubPlanSummary())
    instance.serialize_plan = MagicMock(
        return_value={
            "task_id": "plan-id-001",
            "task_type": "blog_post",
            "stages": [{"stage_name": "Research"}],
            "total_estimated_duration_ms": 45000,
            "total_estimated_cost": 1.25,
        }
    )
    return mock_cls


# ---------------------------------------------------------------------------
# POST /api/tasks/intent
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestCreateTaskFromIntent:
    """Tests for POST /api/tasks/intent."""

    @patch(
        "services.task_planning_service.TaskPlanningService",
        new_callable=_mock_planning_service_class,
    )
    @patch("services.task_intent_router.TaskIntentRouter", new_callable=_mock_intent_router_class)
    def test_success_returns_execution_plan(self, MockRouter, MockPlanner):
        """Happy path: NL input is parsed, plan generated, response returned."""
        client = _make_client()

        resp = client.post(
            f"{_PREFIX}/intent",
            json={"user_input": "Write a blog post about AI trends"},
        )

        assert resp.status_code == 200
        body = resp.json()

        # task_id should be None (not yet created)
        assert body["task_id"] is None

        # intent_request populated
        assert body["intent_request"]["intent_type"] == "content_generation"
        assert body["intent_request"]["task_type"] == "blog_post"
        assert body["intent_request"]["confidence"] == pytest.approx(0.92)

        # execution_plan populated
        assert body["execution_plan"]["title"] == "Blog Post: AI trends"
        assert body["execution_plan"]["estimated_cost"] == "$1.25"
        assert "full_plan" in body["execution_plan"]

        # requires_confirmation = True → ready_to_execute = False
        assert body["ready_to_execute"] is False

    @patch(
        "services.task_planning_service.TaskPlanningService",
        new_callable=_mock_planning_service_class,
    )
    @patch("services.task_intent_router.TaskIntentRouter", new_callable=_mock_intent_router_class)
    def test_success_with_optional_fields(self, MockRouter, MockPlanner):
        """User_context and business_metrics are forwarded to services."""
        client = _make_client()

        resp = client.post(
            f"{_PREFIX}/intent",
            json={
                "user_input": "Write about cloud computing",
                "user_context": {"preference": "technical"},
                "business_metrics": {"budget": 5.0},
            },
        )

        assert resp.status_code == 200
        # Verify services were called with forwarded args
        instance = MockRouter.return_value
        instance.route_user_input.assert_called_once_with(
            "Write about cloud computing", {"preference": "technical"}
        )
        planner_instance = MockPlanner.return_value
        planner_instance.generate_plan.assert_called_once()

    @patch(
        "services.task_planning_service.TaskPlanningService",
        new_callable=_mock_planning_service_class,
    )
    @patch("services.task_intent_router.TaskIntentRouter", new_callable=_mock_intent_router_class)
    def test_ready_to_execute_when_no_confirmation_needed(self, MockRouter, MockPlanner):
        """When intent does not require confirmation, ready_to_execute is True."""
        stub = _StubIntentRequest(requires_confirmation=False)
        MockRouter.return_value.route_user_input = AsyncMock(return_value=stub)

        client = _make_client()
        resp = client.post(
            f"{_PREFIX}/intent",
            json={"user_input": "Quick social post"},
        )

        assert resp.status_code == 200
        assert resp.json()["ready_to_execute"] is True

    def test_empty_user_input_returns_422(self):
        """Pydantic validation: user_input is required and must be a string."""
        client = _make_client()

        resp = client.post(f"{_PREFIX}/intent", json={})
        assert resp.status_code == 422

    def test_missing_body_returns_422(self):
        """No JSON body at all should return 422."""
        client = _make_client()

        resp = client.post(f"{_PREFIX}/intent")
        assert resp.status_code == 422

    @patch(
        "services.task_planning_service.TaskPlanningService",
        new_callable=_mock_planning_service_class,
    )
    @patch("services.task_intent_router.TaskIntentRouter")
    def test_intent_router_failure_returns_500(self, MockRouter, MockPlanner):
        """If TaskIntentRouter.route_user_input raises, endpoint returns 500."""
        instance = MockRouter.return_value
        instance.route_user_input = AsyncMock(side_effect=RuntimeError("NLP model unavailable"))

        client = _make_client()
        resp = client.post(
            f"{_PREFIX}/intent",
            json={"user_input": "Write something"},
        )

        assert resp.status_code == 500
        assert resp.json()["detail"] == "Intent parsing failed"

    @patch("services.task_intent_router.TaskIntentRouter", new_callable=_mock_intent_router_class)
    @patch("services.task_planning_service.TaskPlanningService")
    def test_planning_service_failure_returns_500(self, MockPlanner, MockRouter):
        """If TaskPlanningService.generate_plan raises, endpoint returns 500."""
        instance = MockPlanner.return_value
        instance.generate_plan = AsyncMock(side_effect=ValueError("Invalid plan parameters"))

        client = _make_client()
        resp = client.post(
            f"{_PREFIX}/intent",
            json={"user_input": "Write a post"},
        )

        assert resp.status_code == 500
        assert resp.json()["detail"] == "Intent parsing failed"

    @patch(
        "services.task_planning_service.TaskPlanningService",
        new_callable=_mock_planning_service_class,
    )
    @patch("services.task_intent_router.TaskIntentRouter", new_callable=_mock_intent_router_class)
    def test_warnings_propagated_to_response(self, MockRouter, MockPlanner):
        """Plan warnings should be included in the response."""
        summary = _StubPlanSummary(warnings=["No QA review included"])
        MockPlanner.return_value.plan_to_summary = MagicMock(return_value=summary)

        client = _make_client()
        resp = client.post(
            f"{_PREFIX}/intent",
            json={"user_input": "Write a quick post"},
        )

        assert resp.status_code == 200
        assert resp.json()["warnings"] == ["No QA review included"]
        assert resp.json()["execution_plan"]["warnings"] == ["No QA review included"]


# ---------------------------------------------------------------------------
# POST /api/tasks/confirm-intent
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestConfirmAndExecuteTask:
    """Tests for POST /api/tasks/confirm-intent."""

    def _valid_payload(self, **overrides) -> dict:
        """Build a valid TaskConfirmRequest payload."""
        base = {
            "intent_request": {
                "intent_type": "content_generation",
                "task_type": "blog_post",
                "parameters": {"topic": "AI trends"},
            },
            "execution_plan": {
                "task_id": "plan-id-001",
                "stages": [{"stage_name": "Research"}, {"stage_name": "Creative"}],
            },
            "user_confirmed": True,
        }
        base.update(overrides)
        return base

    def test_success_creates_task_and_returns_response(self):
        """Happy path: confirmed plan creates task in DB and returns pending status."""
        mock_db = make_mock_db()
        client = _make_client(mock_db=mock_db)

        resp = client.post(f"{_PREFIX}/confirm-intent", json=self._valid_payload())

        assert resp.status_code == 200
        body = resp.json()

        # Task ID is a valid UUID string
        assert len(body["task_id"]) == 36  # UUID format
        assert body["status"] == "pending"
        assert "2 stages" in body["message"]
        assert body["execution_plan_id"] == "plan-id-001"

        # DB was called
        mock_db.add_task.assert_called_once()
        call_args = mock_db.add_task.call_args[0][0]
        assert call_args["task_name"] == "AI trends"
        assert call_args["task_type"] == "blog_post"
        assert call_args["status"] == "pending"
        assert call_args["metadata"]["created_from_intent"] is True
        assert call_args["metadata"]["user_confirmed"] is True

    def test_success_with_modifications(self):
        """User modifications are stored in execution metadata."""
        mock_db = make_mock_db()
        client = _make_client(mock_db=mock_db)

        payload = self._valid_payload(modifications={"skip_qa": True})
        resp = client.post(f"{_PREFIX}/confirm-intent", json=payload)

        assert resp.status_code == 200
        call_args = mock_db.add_task.call_args[0][0]
        assert call_args["metadata"]["modifications"] == {"skip_qa": True}

    def test_user_not_confirmed_returns_400(self):
        """If user_confirmed=false, endpoint rejects with 400."""
        client = _make_client()

        payload = self._valid_payload(user_confirmed=False)
        resp = client.post(f"{_PREFIX}/confirm-intent", json=payload)

        assert resp.status_code == 400
        assert resp.json()["detail"] == "User did not confirm execution plan"

    def test_missing_intent_request_returns_422(self):
        """Missing required field intent_request returns 422."""
        client = _make_client()

        resp = client.post(
            f"{_PREFIX}/confirm-intent",
            json={
                "execution_plan": {"stages": []},
                "user_confirmed": True,
            },
        )

        assert resp.status_code == 422

    def test_missing_execution_plan_returns_422(self):
        """Missing required field execution_plan returns 422."""
        client = _make_client()

        resp = client.post(
            f"{_PREFIX}/confirm-intent",
            json={
                "intent_request": {"intent_type": "content_generation"},
                "user_confirmed": True,
            },
        )

        assert resp.status_code == 422

    def test_missing_body_returns_422(self):
        """No JSON body at all should return 422."""
        client = _make_client()

        resp = client.post(f"{_PREFIX}/confirm-intent")
        assert resp.status_code == 422

    def test_db_write_failure_returns_500(self):
        """If db_service.add_task raises, endpoint returns 500."""
        mock_db = make_mock_db()
        mock_db.add_task = AsyncMock(side_effect=RuntimeError("Connection lost"))

        client = _make_client(mock_db=mock_db)
        resp = client.post(f"{_PREFIX}/confirm-intent", json=self._valid_payload())

        assert resp.status_code == 500
        assert resp.json()["detail"] == "Task confirmation failed"

    def test_default_task_name_when_topic_missing(self):
        """When parameters has no topic, task_name defaults to 'Task from Intent'."""
        mock_db = make_mock_db()
        client = _make_client(mock_db=mock_db)

        payload = self._valid_payload()
        payload["intent_request"] = {
            "intent_type": "social_media",
            "task_type": "social_media",
            "parameters": {},  # no topic
        }
        resp = client.post(f"{_PREFIX}/confirm-intent", json=payload)

        assert resp.status_code == 200
        call_args = mock_db.add_task.call_args[0][0]
        assert call_args["task_name"] == "Task from Intent"

    def test_default_task_type_when_missing(self):
        """When intent_request has no task_type, defaults to 'generic'."""
        mock_db = make_mock_db()
        client = _make_client(mock_db=mock_db)

        payload = self._valid_payload()
        payload["intent_request"] = {"intent_type": "unknown"}  # no task_type
        resp = client.post(f"{_PREFIX}/confirm-intent", json=payload)

        assert resp.status_code == 200
        call_args = mock_db.add_task.call_args[0][0]
        assert call_args["task_type"] == "generic"

    def test_execution_plan_id_fallback_to_task_id(self):
        """When execution_plan has no task_id, execution_plan_id falls back to generated task_id."""
        mock_db = make_mock_db()
        client = _make_client(mock_db=mock_db)

        payload = self._valid_payload()
        payload["execution_plan"] = {"stages": []}  # no task_id key
        resp = client.post(f"{_PREFIX}/confirm-intent", json=payload)

        assert resp.status_code == 200
        body = resp.json()
        # execution_plan_id should match the generated task_id
        assert body["execution_plan_id"] == body["task_id"]

    def test_stages_count_in_message(self):
        """Message should reflect the number of stages in the plan."""
        mock_db = make_mock_db()
        client = _make_client(mock_db=mock_db)

        payload = self._valid_payload()
        payload["execution_plan"]["stages"] = [
            {"stage_name": "Research"},
            {"stage_name": "Creative"},
            {"stage_name": "QA"},
        ]
        resp = client.post(f"{_PREFIX}/confirm-intent", json=payload)

        assert resp.status_code == 200
        assert "3 stages" in resp.json()["message"]


# ---------------------------------------------------------------------------
# Auth: unauthenticated requests
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestIntentRoutesAuth:
    """Unauthenticated requests should be rejected."""

    def test_intent_unauthenticated_returns_error(self):
        """POST /intent without auth override should fail."""
        client = _make_client(authenticated=False)

        resp = client.post(
            f"{_PREFIX}/intent",
            json={"user_input": "Write a blog post"},
        )

        # FastAPI returns 401 or 403 depending on the auth dependency
        assert resp.status_code in (401, 403, 500)

    def test_confirm_intent_unauthenticated_returns_error(self):
        """POST /confirm-intent without auth override should fail."""
        client = _make_client(authenticated=False)

        resp = client.post(
            f"{_PREFIX}/confirm-intent",
            json={
                "intent_request": {"intent_type": "content_generation"},
                "execution_plan": {"stages": []},
                "user_confirmed": True,
            },
        )

        assert resp.status_code in (401, 403, 500)
