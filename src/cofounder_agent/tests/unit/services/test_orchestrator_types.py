"""Unit tests for services/orchestrator_types.py.

Covers:
- RequestType enum values and str behaviour
- ExecutionStatus enum values and str behaviour
- Request dataclass defaults and field assignment
- ExecutionContext dataclass construction
- ExecutionResult dataclass defaults and to_dict()
"""

from datetime import datetime, timezone

import pytest

from services.orchestrator_types import (
    ExecutionContext,
    ExecutionResult,
    ExecutionStatus,
    Request,
    RequestType,
)

# ---------------------------------------------------------------------------
# RequestType
# ---------------------------------------------------------------------------


class TestRequestType:
    def test_all_members_present(self) -> None:
        expected = {
            "CONTENT_CREATION",
            "CONTENT_SUBTASK",
            "FINANCIAL_ANALYSIS",
            "COMPLIANCE_CHECK",
            "TASK_MANAGEMENT",
            "INFORMATION_RETRIEVAL",
            "DECISION_SUPPORT",
            "SYSTEM_OPERATION",
            "INTERVENTION",
        }
        assert {m.name for m in RequestType} == expected

    def test_is_str_subclass(self) -> None:
        assert isinstance(RequestType.CONTENT_CREATION, str)

    def test_string_values(self) -> None:
        assert RequestType.CONTENT_CREATION == "content_creation"
        assert RequestType.CONTENT_SUBTASK == "content_subtask"
        assert RequestType.FINANCIAL_ANALYSIS == "financial_analysis"
        assert RequestType.COMPLIANCE_CHECK == "compliance_check"
        assert RequestType.TASK_MANAGEMENT == "task_management"
        assert RequestType.INFORMATION_RETRIEVAL == "information_retrieval"
        assert RequestType.DECISION_SUPPORT == "decision_support"
        assert RequestType.SYSTEM_OPERATION == "system_operation"
        assert RequestType.INTERVENTION == "intervention"

    def test_round_trip_from_value(self) -> None:
        for member in RequestType:
            assert RequestType(member.value) is member

    def test_usable_as_dict_key(self) -> None:
        d = {RequestType.CONTENT_CREATION: "blog"}
        assert d["content_creation"] == "blog"  # type: ignore[index]


# ---------------------------------------------------------------------------
# ExecutionStatus
# ---------------------------------------------------------------------------


class TestExecutionStatus:
    def test_all_members_present(self) -> None:
        expected = {
            "PENDING",
            "PLANNING",
            "EXECUTING",
            "ASSESSING",
            "REFINEMENT",
            "PENDING_APPROVAL",
            "COMPLETED",
            "FAILED",
            "CANCELLED",
        }
        assert {m.name for m in ExecutionStatus} == expected

    def test_is_str_subclass(self) -> None:
        assert isinstance(ExecutionStatus.PENDING, str)

    def test_string_values(self) -> None:
        assert ExecutionStatus.PENDING == "pending"
        assert ExecutionStatus.PLANNING == "planning"
        assert ExecutionStatus.EXECUTING == "executing"
        assert ExecutionStatus.ASSESSING == "assessing"
        assert ExecutionStatus.REFINEMENT == "refinement"
        assert ExecutionStatus.PENDING_APPROVAL == "pending_approval"
        assert ExecutionStatus.COMPLETED == "completed"
        assert ExecutionStatus.FAILED == "failed"
        assert ExecutionStatus.CANCELLED == "cancelled"

    def test_round_trip_from_value(self) -> None:
        for member in ExecutionStatus:
            assert ExecutionStatus(member.value) is member


# ---------------------------------------------------------------------------
# Request dataclass
# ---------------------------------------------------------------------------


class TestRequest:
    def _make_request(self, **overrides) -> Request:
        defaults = dict(
            request_id="req-001",
            original_text="Write a blog post",
            request_type=RequestType.CONTENT_CREATION,
            extracted_intent="create blog post",
        )
        defaults.update(overrides)
        return Request(**defaults)  # type: ignore[arg-type]

    def test_required_fields_assigned(self) -> None:
        req = self._make_request()
        assert req.request_id == "req-001"
        assert req.original_text == "Write a blog post"
        assert req.request_type is RequestType.CONTENT_CREATION
        assert req.extracted_intent == "create blog post"

    def test_optional_defaults(self) -> None:
        req = self._make_request()
        assert req.parameters == {}
        assert req.context == {}
        assert req.user_id is None

    def test_created_at_defaults_to_utc_now(self) -> None:
        before = datetime.now(timezone.utc)
        req = self._make_request()
        after = datetime.now(timezone.utc)
        assert before <= req.created_at <= after

    def test_created_at_is_timezone_aware(self) -> None:
        req = self._make_request()
        assert req.created_at.tzinfo is not None

    def test_parameters_not_shared_between_instances(self) -> None:
        r1 = self._make_request()
        r2 = self._make_request()
        r1.parameters["key"] = "value"
        assert "key" not in r2.parameters

    def test_context_not_shared_between_instances(self) -> None:
        r1 = self._make_request()
        r2 = self._make_request()
        r1.context["x"] = 1
        assert "x" not in r2.context

    def test_optional_fields_explicit_values(self) -> None:
        req = self._make_request(
            parameters={"topic": "AI"},
            context={"locale": "en"},
            user_id="user-42",
        )
        assert req.parameters == {"topic": "AI"}
        assert req.context == {"locale": "en"}
        assert req.user_id == "user-42"

    def test_all_request_types_accepted(self) -> None:
        for rtype in RequestType:
            req = self._make_request(request_type=rtype)
            assert req.request_type is rtype


# ---------------------------------------------------------------------------
# ExecutionContext dataclass
# ---------------------------------------------------------------------------


class TestExecutionContext:
    def test_minimal_construction(self) -> None:
        ctx = ExecutionContext(
            request_id="req-ctx-001",
            request_type=RequestType.TASK_MANAGEMENT,
        )
        assert ctx.request_id == "req-ctx-001"
        assert ctx.request_type is RequestType.TASK_MANAGEMENT

    def test_optional_service_defaults_to_none(self) -> None:
        ctx = ExecutionContext(
            request_id="r",
            request_type=RequestType.SYSTEM_OPERATION,
        )
        assert ctx.database_service is None
        assert ctx.model_router is None
        assert ctx.quality_service is None
        assert ctx.memory_system is None

    def test_orchestrator_agents_default_empty(self) -> None:
        ctx = ExecutionContext(
            request_id="r",
            request_type=RequestType.SYSTEM_OPERATION,
        )
        assert ctx.orchestrator_agents == {}

    def test_orchestrator_agents_not_shared(self) -> None:
        c1 = ExecutionContext(request_id="r1", request_type=RequestType.SYSTEM_OPERATION)
        c2 = ExecutionContext(request_id="r2", request_type=RequestType.SYSTEM_OPERATION)
        c1.orchestrator_agents["agent"] = object()
        assert "agent" not in c2.orchestrator_agents

    def test_service_injection(self) -> None:
        mock_db = object()
        mock_router = object()
        ctx = ExecutionContext(
            request_id="r",
            request_type=RequestType.FINANCIAL_ANALYSIS,
            database_service=mock_db,
            model_router=mock_router,
        )
        assert ctx.database_service is mock_db
        assert ctx.model_router is mock_router


# ---------------------------------------------------------------------------
# ExecutionResult dataclass + to_dict()
# ---------------------------------------------------------------------------


class TestExecutionResult:
    def _make_result(self, **overrides) -> ExecutionResult:
        defaults = dict(
            request_id="req-res-001",
            request_type=RequestType.CONTENT_CREATION,
            status=ExecutionStatus.COMPLETED,
            output={"html": "<p>Hello</p>"},
        )
        defaults.update(overrides)
        return ExecutionResult(**defaults)  # type: ignore[arg-type]

    def test_required_fields(self) -> None:
        result = self._make_result()
        assert result.request_id == "req-res-001"
        assert result.request_type is RequestType.CONTENT_CREATION
        assert result.status is ExecutionStatus.COMPLETED
        assert result.output == {"html": "<p>Hello</p>"}

    def test_optional_defaults(self) -> None:
        result = self._make_result()
        assert result.task_id is None
        assert result.quality_score is None
        assert result.passed_quality is None
        assert result.feedback is None
        assert result.duration_ms == 0
        assert result.cost_usd == 0.0
        assert result.refinement_attempts == 0
        assert result.training_example is None
        assert result.metadata == {}

    def test_created_at_is_utc_aware(self) -> None:
        before = datetime.now(timezone.utc)
        result = self._make_result()
        after = datetime.now(timezone.utc)
        assert before <= result.created_at <= after
        assert result.created_at.tzinfo is not None

    def test_metadata_not_shared(self) -> None:
        r1 = self._make_result()
        r2 = self._make_result()
        r1.metadata["k"] = "v"
        assert "k" not in r2.metadata

    def test_explicit_optional_fields(self) -> None:
        result = self._make_result(
            task_id="task-99",
            quality_score=0.87,
            passed_quality=True,
            feedback="Looks good",
            duration_ms=1234.5,
            cost_usd=0.003,
            refinement_attempts=2,
            training_example={"q": "Q", "a": "A"},
            metadata={"source": "pipeline"},
        )
        assert result.task_id == "task-99"
        assert result.quality_score == pytest.approx(0.87)
        assert result.passed_quality is True
        assert result.feedback == "Looks good"
        assert result.duration_ms == pytest.approx(1234.5)
        assert result.cost_usd == pytest.approx(0.003)
        assert result.refinement_attempts == 2
        assert result.training_example == {"q": "Q", "a": "A"}
        assert result.metadata == {"source": "pipeline"}

    # --- to_dict() ---

    def test_to_dict_keys_present(self) -> None:
        result = self._make_result()
        d = result.to_dict()
        expected_keys = {
            "request_id",
            "request_type",
            "status",
            "output",
            "task_id",
            "quality_score",
            "passed_quality",
            "feedback",
            "duration_ms",
            "cost_usd",
            "refinement_attempts",
            "training_example",
            "metadata",
            "created_at",
        }
        assert set(d.keys()) == expected_keys

    def test_to_dict_enum_serialised_as_value(self) -> None:
        result = self._make_result()
        d = result.to_dict()
        assert d["request_type"] == "content_creation"
        assert d["status"] == "completed"

    def test_to_dict_created_at_is_isoformat_string(self) -> None:
        result = self._make_result()
        d = result.to_dict()
        # Should be a parseable ISO-8601 string
        parsed = datetime.fromisoformat(d["created_at"])
        assert parsed.tzinfo is not None

    def test_to_dict_none_defaults(self) -> None:
        result = self._make_result()
        d = result.to_dict()
        assert d["task_id"] is None
        assert d["quality_score"] is None
        assert d["passed_quality"] is None
        assert d["feedback"] is None
        assert d["training_example"] is None

    def test_to_dict_numeric_defaults(self) -> None:
        result = self._make_result()
        d = result.to_dict()
        assert d["duration_ms"] == 0
        assert d["cost_usd"] == 0.0
        assert d["refinement_attempts"] == 0

    def test_to_dict_output_preserved(self) -> None:
        payload = {"html": "<p>Test</p>", "word_count": 3}
        result = self._make_result(output=payload)
        assert result.to_dict()["output"] == payload

    def test_to_dict_metadata_preserved(self) -> None:
        result = self._make_result(metadata={"pipeline": "v2"})
        assert result.to_dict()["metadata"] == {"pipeline": "v2"}

    def test_to_dict_full_optional_fields(self) -> None:
        result = self._make_result(
            task_id="t-1",
            quality_score=0.9,
            passed_quality=True,
            feedback="Great",
            duration_ms=500.0,
            cost_usd=0.01,
            refinement_attempts=1,
            training_example={"x": 1},
        )
        d = result.to_dict()
        assert d["task_id"] == "t-1"
        assert d["quality_score"] == pytest.approx(0.9)
        assert d["passed_quality"] is True
        assert d["feedback"] == "Great"
        assert d["duration_ms"] == pytest.approx(500.0)
        assert d["cost_usd"] == pytest.approx(0.01)
        assert d["refinement_attempts"] == 1
        assert d["training_example"] == {"x": 1}

    def test_all_statuses_serialised_correctly(self) -> None:
        for status in ExecutionStatus:
            result = self._make_result(status=status)
            assert result.to_dict()["status"] == status.value

    def test_all_request_types_serialised_correctly(self) -> None:
        for rtype in RequestType:
            result = self._make_result(request_type=rtype)
            assert result.to_dict()["request_type"] == rtype.value
