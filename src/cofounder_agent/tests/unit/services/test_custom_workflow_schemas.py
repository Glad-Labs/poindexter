"""
Unit tests for custom_workflow_schemas.py

Tests field validation, validators, and model behaviour for workflow schemas.
"""

import pytest
from datetime import datetime, timezone
from pydantic import ValidationError

from schemas.custom_workflow_schemas import (
    AvailablePhase,
    AvailablePhasesResponse,
    CustomWorkflow,
    InputTrace,
    PhaseConfig,
    PhaseInputField,
    PhaseResult,
    WorkflowExecutionRequest,
    WorkflowExecutionResponse,
    WorkflowListPageResponse,
    WorkflowListResponse,
    WorkflowPhase,
    WorkflowValidationResult,
)


# ---------------------------------------------------------------------------
# PhaseConfig
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestPhaseConfig:
    def _valid(self, **kwargs):
        defaults = {
            "name": "research",
            "agent": "content_agent",
        }
        defaults.update(kwargs)
        return PhaseConfig(**defaults)  # type: ignore[arg-type]

    def test_valid_minimal(self):
        phase = self._valid()
        assert phase.timeout_seconds == 300
        assert phase.max_retries == 3
        assert phase.skip_on_error is False
        assert phase.required is True
        assert phase.quality_threshold is None
        assert phase.metadata == {}

    def test_name_cannot_be_empty(self):
        with pytest.raises(ValidationError):
            self._valid(name="   ")

    def test_name_cannot_have_spaces(self):
        with pytest.raises(ValidationError):
            self._valid(name="my phase")

    def test_name_with_underscore(self):
        phase = self._valid(name="my_phase")
        assert phase.name == "my_phase"

    def test_timeout_minimum(self):
        phase = self._valid(timeout_seconds=10)
        assert phase.timeout_seconds == 10

    def test_timeout_maximum(self):
        phase = self._valid(timeout_seconds=3600)
        assert phase.timeout_seconds == 3600

    def test_timeout_below_minimum_raises(self):
        with pytest.raises(ValidationError):
            self._valid(timeout_seconds=9)

    def test_timeout_above_maximum_raises(self):
        with pytest.raises(ValidationError):
            self._valid(timeout_seconds=3601)

    def test_max_retries_minimum(self):
        phase = self._valid(max_retries=0)
        assert phase.max_retries == 0

    def test_max_retries_maximum(self):
        phase = self._valid(max_retries=10)
        assert phase.max_retries == 10

    def test_max_retries_above_maximum_raises(self):
        with pytest.raises(ValidationError):
            self._valid(max_retries=11)

    def test_quality_threshold_valid_bounds(self):
        phase = self._valid(quality_threshold=0.0)
        assert phase.quality_threshold == 0.0
        phase = self._valid(quality_threshold=1.0)
        assert phase.quality_threshold == 1.0

    def test_quality_threshold_out_of_bounds_raises(self):
        with pytest.raises(ValidationError):
            self._valid(quality_threshold=1.1)
        with pytest.raises(ValidationError):
            self._valid(quality_threshold=-0.1)


# ---------------------------------------------------------------------------
# WorkflowPhase
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestWorkflowPhase:
    def _valid(self, **kwargs):
        defaults = {"index": 0, "name": "research"}
        defaults.update(kwargs)
        return WorkflowPhase(**defaults)

    def test_valid_minimal(self):
        phase = self._valid()
        assert phase.index == 0
        assert phase.user_inputs == {}
        assert phase.input_mapping == {}
        assert phase.skip is False
        assert phase.model_overrides is None

    def test_name_cannot_be_empty(self):
        with pytest.raises(ValidationError):
            self._valid(name="")

    def test_name_cannot_have_special_chars(self):
        with pytest.raises(ValidationError):
            self._valid(name="my-phase")

    def test_with_user_inputs(self):
        phase = self._valid(user_inputs={"topic": "AI"})
        assert phase.user_inputs == {"topic": "AI"}

    def test_with_input_mapping(self):
        phase = self._valid(input_mapping={"research_output": "research.output"})
        assert "research_output" in phase.input_mapping


# ---------------------------------------------------------------------------
# InputTrace
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestInputTrace:
    def test_defaults(self):
        trace = InputTrace()  # type: ignore[call-arg]
        assert trace.source_phase is None
        assert trace.source_field is None
        assert trace.user_provided is False
        assert trace.auto_mapped is False

    def test_user_provided(self):
        trace = InputTrace(user_provided=True)  # type: ignore[call-arg]
        assert trace.user_provided is True

    def test_auto_mapped_from_source(self):
        trace = InputTrace(  # type: ignore[call-arg]
            source_phase="research",
            source_field="output",
            auto_mapped=True,
        )
        assert trace.source_phase == "research"
        assert trace.auto_mapped is True


# ---------------------------------------------------------------------------
# PhaseResult
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestPhaseResult:
    def test_valid_minimal(self):
        result = PhaseResult(status="completed")  # type: ignore[call-arg]
        assert result.output == {}
        assert result.error is None
        assert result.execution_time_ms == 0.0
        assert result.model_used is None
        assert result.tokens_used is None
        assert result.input_trace == {}
        assert result.metadata == {}

    def test_failed_status_with_error(self):
        result = PhaseResult(status="failed", error="Connection timeout")  # type: ignore[call-arg]
        assert result.status == "failed"
        assert result.error == "Connection timeout"

    def test_with_output_and_metadata(self):
        result = PhaseResult(  # type: ignore[call-arg]
            status="completed",
            output={"content": "research result"},
            model_used="gpt-4",
            tokens_used=500,
            execution_time_ms=1200.5,
        )
        assert result.tokens_used == 500
        assert result.execution_time_ms == 1200.5


# ---------------------------------------------------------------------------
# CustomWorkflow
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestCustomWorkflow:
    def _valid_phase(self, name="research"):
        return {"name": name, "agent": "content_agent"}

    def _valid(self, **kwargs):
        defaults = {
            "name": "My Blog Workflow",
            "description": "A workflow for creating blog posts",
            "phases": [self._valid_phase()],
        }
        defaults.update(kwargs)
        return CustomWorkflow(**defaults)

    def test_valid_minimal(self):
        wf = self._valid()
        assert wf.id is None
        assert wf.owner_id is None
        assert wf.tags == []
        assert wf.is_template is False

    def test_name_cannot_be_empty(self):
        with pytest.raises(ValidationError):
            self._valid(name="   ")

    def test_name_too_long_raises(self):
        with pytest.raises(ValidationError):
            self._valid(name="x" * 256)

    def test_description_cannot_be_empty(self):
        with pytest.raises(ValidationError):
            self._valid(description="   ")

    def test_empty_phases_raises(self):
        with pytest.raises(ValidationError):
            self._valid(phases=[])

    def test_duplicate_phase_names_raises(self):
        with pytest.raises(ValidationError):
            self._valid(
                phases=[
                    self._valid_phase("research"),
                    self._valid_phase("research"),  # duplicate
                ]
            )

    def test_unique_phase_names_pass(self):
        wf = self._valid(
            phases=[
                self._valid_phase("research"),
                self._valid_phase("draft"),
                self._valid_phase("assess"),
            ]
        )
        assert len(wf.phases) == 3

    def test_with_metadata(self):
        wf = self._valid(
            id="wf-123",
            owner_id="user-456",
            tags=["blog", "ai"],
            is_template=True,
        )
        assert wf.id == "wf-123"
        assert wf.is_template is True
        assert "blog" in wf.tags


# ---------------------------------------------------------------------------
# WorkflowExecutionRequest
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestWorkflowExecutionRequest:
    def test_valid_with_workflow_id(self):
        req = WorkflowExecutionRequest(  # type: ignore[call-arg]
            workflow_id="wf-123",
            input_data={"topic": "AI in Finance"},
        )
        assert req.workflow_id == "wf-123"
        assert req.skip_phases is None
        assert req.quality_threshold is None

    def test_valid_minimal(self):
        req = WorkflowExecutionRequest()  # type: ignore[call-arg]
        assert req.workflow_id is None
        assert req.phases is None
        assert req.input_data == {}

    def test_quality_threshold_bounds(self):
        req = WorkflowExecutionRequest(quality_threshold=0.0)  # type: ignore[call-arg]
        assert req.quality_threshold == 0.0
        req = WorkflowExecutionRequest(quality_threshold=1.0)  # type: ignore[call-arg]
        assert req.quality_threshold == 1.0

    def test_quality_threshold_out_of_bounds_raises(self):
        with pytest.raises(ValidationError):
            WorkflowExecutionRequest(quality_threshold=1.1)  # type: ignore[call-arg]


# ---------------------------------------------------------------------------
# WorkflowExecutionResponse
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestWorkflowExecutionResponse:
    def test_valid(self):
        resp = WorkflowExecutionResponse(  # type: ignore[call-arg]
            workflow_id="wf-123",
            execution_id="exec-456",
            status="running",
            started_at=datetime.now(timezone.utc),
            phases=["research", "draft", "assess"],
        )
        assert resp.progress_percent == 0
        assert resp.phase_results == {}
        assert resp.final_output is None
        assert resp.error_message is None

    def test_progress_percent_bounds(self):
        resp = WorkflowExecutionResponse(  # type: ignore[call-arg]
            workflow_id="wf-1",
            execution_id="exec-1",
            status="running",
            started_at=datetime.now(timezone.utc),
            phases=["research"],
            progress_percent=0,
        )
        assert resp.progress_percent == 0

    def test_progress_percent_too_high_raises(self):
        with pytest.raises(ValidationError):
            WorkflowExecutionResponse(  # type: ignore[call-arg]
                workflow_id="wf-1",
                execution_id="exec-1",
                status="running",
                started_at=datetime.now(timezone.utc),
                phases=["research"],
                progress_percent=101,
            )

    def test_progress_percent_negative_raises(self):
        with pytest.raises(ValidationError):
            WorkflowExecutionResponse(  # type: ignore[call-arg]
                workflow_id="wf-1",
                execution_id="exec-1",
                status="running",
                started_at=datetime.now(timezone.utc),
                phases=["research"],
                progress_percent=-1,
            )


# ---------------------------------------------------------------------------
# WorkflowValidationResult
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestWorkflowValidationResult:
    def test_valid(self):
        result = WorkflowValidationResult(valid=True)
        assert result.errors == []
        assert result.warnings == []

    def test_invalid_with_errors(self):
        result = WorkflowValidationResult(
            valid=False,
            errors=["Phase 'research' is missing required agent"],
            warnings=["Phase timeout is very short"],
        )
        assert not result.valid
        assert len(result.errors) == 1
        assert len(result.warnings) == 1


# ---------------------------------------------------------------------------
# WorkflowListResponse and WorkflowListPageResponse
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestWorkflowListPageResponse:
    def _make_list_item(self):
        return WorkflowListResponse(  # type: ignore[call-arg]
            id="wf-123",
            name="My Workflow",
            description="A description",
            phase_count=3,
            created_at=datetime.now(timezone.utc),
            updated_at=datetime.now(timezone.utc),
        )

    def test_valid(self):
        page = WorkflowListPageResponse(
            workflows=[self._make_list_item()],
            total_count=1,
            page=1,
            page_size=20,
            has_next=False,
        )
        assert page.total_count == 1
        assert not page.has_next

    def test_empty_page(self):
        page = WorkflowListPageResponse(
            workflows=[],
            total_count=0,
            page=1,
            page_size=20,
            has_next=False,
        )
        assert len(page.workflows) == 0


# ---------------------------------------------------------------------------
# PhaseInputField and AvailablePhase
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestPhaseInputField:
    def test_valid_defaults(self):
        field = PhaseInputField(key="topic", label="Topic")  # type: ignore[call-arg]
        assert field.input_type == "text"
        assert field.required is False
        assert field.placeholder is None
        assert field.default_value is None
        assert field.options == []

    def test_select_type_with_options(self):
        field = PhaseInputField(  # type: ignore[call-arg]
            key="style",
            label="Content Style",
            input_type="select",
            options=["technical", "narrative", "educational"],
        )
        assert len(field.options) == 3


@pytest.mark.unit
class TestAvailablePhase:
    def test_valid(self):
        phase = AvailablePhase(  # type: ignore[call-arg]
            name="research",
            description="Conducts web research on the topic",
            category="content",
            default_timeout_seconds=300,
            compatible_agents=["content_agent"],
            capabilities=["web_search"],
            default_retries=3,
        )
        assert phase.supports_model_selection is True
        assert phase.input_fields == []
        assert phase.version == "1.0"


@pytest.mark.unit
class TestAvailablePhasesResponse:
    def test_valid(self):
        phase = AvailablePhase(  # type: ignore[call-arg]
            name="research",
            description="Research phase",
            category="content",
            default_timeout_seconds=300,
            compatible_agents=["content_agent"],
            capabilities=["web_search"],
            default_retries=3,
        )
        resp = AvailablePhasesResponse(phases=[phase], total_count=1)
        assert resp.total_count == 1
        assert len(resp.phases) == 1
