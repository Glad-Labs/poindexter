"""Unit tests for services/workflow_validator.py.

Covers WorkflowValidator's public surface — validate_workflow,
validate_for_execution, and the sub-validators that check phase
existence, sequential indices, duplicate names, mappings, timeouts,
and retries. Uses a stub registry + stub mapper so tests do not
depend on the singleton PhaseRegistry contents.
"""

from typing import Any, Dict, List, Optional
from unittest.mock import MagicMock

import pytest

from schemas.custom_workflow_schemas import CustomWorkflow, WorkflowPhase
from services.phase_mapper import PhaseMappingError
from services.phase_registry import (
    InputField,
    InputType,
    OutputField,
    PhaseDefinition,
)
from services.workflow_validator import (
    WorkflowValidationError,
    WorkflowValidator,
)


# ---------------------------------------------------------------------------
# Stub registry / mapper that don't touch the real singleton
# ---------------------------------------------------------------------------


def _make_phase_def(
    name: str,
    inputs: Optional[Dict[str, bool]] = None,
    outputs: Optional[List[str]] = None,
    timeout: int = 300,
    retries: int = 3,
) -> PhaseDefinition:
    """Create a PhaseDefinition with the given input/output schema.

    inputs: dict of input_key -> required (bool)
    outputs: list of output keys
    """
    input_schema = {}
    for key, required in (inputs or {}).items():
        input_schema[key] = InputField(
            key=key, label=key, input_type=InputType.TEXT, required=required
        )
    output_schema = {
        key: OutputField(key=key, label=key) for key in (outputs or [])
    }
    return PhaseDefinition(
        name=name,
        agent_type="generic",
        description="test phase",
        input_schema=input_schema,
        output_schema=output_schema,
        timeout_seconds=timeout,
        max_retries=retries,
    )


class StubRegistry:
    def __init__(self, phases: Dict[str, PhaseDefinition]):
        self._phases = phases

    def phase_exists(self, name: str) -> bool:
        return name in self._phases

    def get_phase(self, name: str) -> Optional[PhaseDefinition]:
        return self._phases.get(name)


class StubMapper:
    """Fake PhaseMapper. Returns whatever map_phases is configured to return."""

    def __init__(self, return_mapping: Optional[Dict[str, str]] = None,
                 raise_error: Optional[Exception] = None):
        self._mapping = return_mapping or {}
        self._raise = raise_error
        self.calls = []

    def map_phases(self, prev_name, curr_name, user_overrides=None):
        self.calls.append((prev_name, curr_name, user_overrides))
        if self._raise:
            raise self._raise
        return self._mapping


def _make_workflow(phases: List[WorkflowPhase], name="Test WF") -> CustomWorkflow:
    return CustomWorkflow(name=name, description="A workflow for testing", phases=phases)


# ---------------------------------------------------------------------------
# WorkflowValidationError class
# ---------------------------------------------------------------------------


class TestWorkflowValidationError:
    def test_is_exception_subclass(self):
        e = WorkflowValidationError("boom")
        assert isinstance(e, Exception)
        assert str(e) == "boom"


# ---------------------------------------------------------------------------
# validate_workflow — overall integration of sub-validators
# ---------------------------------------------------------------------------


class TestValidateWorkflowEmpty:
    def test_no_phases_returns_invalid(self):
        # CustomWorkflow validator rejects empty phases at construction time;
        # bypass it by constructing with model_construct
        workflow = CustomWorkflow.model_construct(
            name="Empty", description="No phases", phases=[]
        )
        validator = WorkflowValidator(registry=StubRegistry({}), mapper=StubMapper())
        valid, errors, warnings = validator.validate_workflow(workflow)
        assert valid is False
        assert any("at least one phase" in e for e in errors)


class TestValidateWorkflowPhaseRegistry:
    def test_unknown_phase_is_error(self):
        registry = StubRegistry({})  # nothing registered
        validator = WorkflowValidator(registry=registry, mapper=StubMapper())
        wf = _make_workflow([WorkflowPhase(index=0, name="not_a_phase")])
        valid, errors, _ = validator.validate_workflow(wf)
        assert valid is False
        assert any("not_a_phase" in e and "not found" in e for e in errors)

    def test_known_phase_valid(self):
        phase_def = _make_phase_def("draft", inputs={"topic": False})
        registry = StubRegistry({"draft": phase_def})
        validator = WorkflowValidator(registry=registry, mapper=StubMapper())
        wf = _make_workflow([WorkflowPhase(index=0, name="draft")])
        valid, errors, _ = validator.validate_workflow(wf)
        assert valid is True
        assert errors == []

    def test_skipped_phase_warns(self):
        phase_def = _make_phase_def("draft")
        registry = StubRegistry({"draft": phase_def})
        validator = WorkflowValidator(registry=registry, mapper=StubMapper())
        wf = _make_workflow([WorkflowPhase(index=0, name="draft", skip=True)])
        valid, _, warnings = validator.validate_workflow(wf)
        assert valid is True
        assert any("marked to skip" in w for w in warnings)


class TestValidateWorkflowIndices:
    def test_non_sequential_indices_is_error(self):
        registry = StubRegistry({
            "draft": _make_phase_def("draft", outputs=["content"]),
            "review": _make_phase_def("review", inputs={"content": False}),
        })
        wf = _make_workflow([
            WorkflowPhase(index=0, name="draft"),
            WorkflowPhase(index=5, name="review"),  # gap → not sequential
        ])
        validator = WorkflowValidator(registry=registry, mapper=StubMapper())
        valid, errors, _ = validator.validate_workflow(wf)
        assert valid is False
        assert any("sequential" in e for e in errors)


class TestValidateWorkflowDuplicateNames:
    def test_duplicate_phase_names_is_error(self):
        # CustomWorkflow validator rejects duplicates at construction; bypass
        registry = StubRegistry({"draft": _make_phase_def("draft")})
        validator = WorkflowValidator(registry=registry, mapper=StubMapper())
        wf = CustomWorkflow.model_construct(
            name="Dup", description="dup phases", phases=[
                WorkflowPhase(index=0, name="draft"),
                WorkflowPhase(index=1, name="draft"),
            ]
        )
        valid, errors, _ = validator.validate_workflow(wf)
        assert valid is False
        assert any("Duplicate phase names" in e for e in errors)


# ---------------------------------------------------------------------------
# validate_workflow — phase-to-phase mapping
# ---------------------------------------------------------------------------


class TestPhaseMappingValidation:
    def test_first_phase_required_input_warns_only(self):
        """Phase 0 missing required inputs should warn (not error) — runtime fills them."""
        phase_def = _make_phase_def(
            "draft", inputs={"topic": True}
        )
        registry = StubRegistry({"draft": phase_def})
        validator = WorkflowValidator(registry=registry, mapper=StubMapper())
        wf = _make_workflow([WorkflowPhase(index=0, name="draft", user_inputs={})])
        valid, errors, warnings = validator.validate_workflow(wf)
        assert valid is True
        assert errors == []
        assert any("topic" in w and "runtime" in w for w in warnings)

    def test_satisfied_required_input_no_warning(self):
        phase_def = _make_phase_def("draft", inputs={"topic": True})
        registry = StubRegistry({"draft": phase_def})
        validator = WorkflowValidator(registry=registry, mapper=StubMapper())
        wf = _make_workflow([
            WorkflowPhase(index=0, name="draft", user_inputs={"topic": "AI"}),
        ])
        valid, errors, warnings = validator.validate_workflow(wf)
        assert valid is True
        assert not any("topic" in w for w in warnings)

    def test_phase_chain_with_valid_mapping(self):
        """Two phases where the second one's required input maps from the first's output."""
        draft = _make_phase_def("draft", inputs={"topic": False}, outputs=["content"])
        review = _make_phase_def("review", inputs={"content": True}, outputs=["score"])
        registry = StubRegistry({"draft": draft, "review": review})
        # Mapper says review's "content" input maps from draft's "content" output
        mapper = StubMapper(return_mapping={"content": "content"})
        validator = WorkflowValidator(registry=registry, mapper=mapper)
        wf = _make_workflow([
            WorkflowPhase(index=0, name="draft"),
            WorkflowPhase(index=1, name="review"),
        ])
        valid, errors, _ = validator.validate_workflow(wf)
        assert valid is True
        assert errors == []
        # Mapper was called for the second phase
        assert len(mapper.calls) == 1
        assert mapper.calls[0][:2] == ("draft", "review")

    def test_required_input_not_satisfied_is_error(self):
        """Phase 1 has required input that isn't in user_inputs, initial_inputs, or mapping."""
        draft = _make_phase_def("draft", outputs=["content"])
        review = _make_phase_def("review", inputs={"different_field": True})
        registry = StubRegistry({"draft": draft, "review": review})
        mapper = StubMapper(return_mapping={})  # empty mapping
        validator = WorkflowValidator(registry=registry, mapper=mapper)
        wf = _make_workflow([
            WorkflowPhase(index=0, name="draft"),
            WorkflowPhase(index=1, name="review"),
        ])
        valid, errors, _ = validator.validate_workflow(wf)
        assert valid is False
        assert any("different_field" in e and "auto-mapped" in e for e in errors)

    def test_initial_inputs_satisfy_required(self):
        """Required inputs can come from initial_inputs at execution time."""
        draft = _make_phase_def("draft", outputs=["content"])
        review = _make_phase_def("review", inputs={"x": True})
        registry = StubRegistry({"draft": draft, "review": review})
        mapper = StubMapper(return_mapping={})
        validator = WorkflowValidator(registry=registry, mapper=mapper)
        wf = _make_workflow([
            WorkflowPhase(index=0, name="draft"),
            WorkflowPhase(index=1, name="review"),
        ])
        valid, errors, _ = validator.validate_workflow(wf, initial_inputs={"x": "value"})
        assert valid is True

    def test_target_key_not_in_input_schema_is_error(self):
        draft = _make_phase_def("draft", outputs=["content"])
        review = _make_phase_def("review", inputs={"real_input": True})
        registry = StubRegistry({"draft": draft, "review": review})
        # Mapper claims to satisfy "wrong_key" which doesn't exist on review
        mapper = StubMapper(return_mapping={"wrong_key": "content"})
        validator = WorkflowValidator(registry=registry, mapper=mapper)
        wf = _make_workflow([
            WorkflowPhase(index=0, name="draft"),
            WorkflowPhase(index=1, name="review", user_inputs={"real_input": "x"}),
        ])
        valid, errors, _ = validator.validate_workflow(wf)
        assert valid is False
        assert any("wrong_key" in e and "not found" in e for e in errors)

    def test_source_key_not_in_output_schema_is_error(self):
        draft = _make_phase_def("draft", outputs=["content"])
        review = _make_phase_def("review", inputs={"real_input": True})
        registry = StubRegistry({"draft": draft, "review": review})
        mapper = StubMapper(return_mapping={"real_input": "no_such_output"})
        validator = WorkflowValidator(registry=registry, mapper=mapper)
        wf = _make_workflow([
            WorkflowPhase(index=0, name="draft"),
            WorkflowPhase(index=1, name="review"),
        ])
        valid, errors, _ = validator.validate_workflow(wf)
        assert valid is False
        assert any("no_such_output" in e for e in errors)

    def test_mapper_raises_phase_mapping_error_becomes_validation_error(self):
        draft = _make_phase_def("draft", outputs=["content"])
        review = _make_phase_def("review", inputs={"content": False})
        registry = StubRegistry({"draft": draft, "review": review})
        mapper = StubMapper(raise_error=PhaseMappingError("incompatible types"))
        validator = WorkflowValidator(registry=registry, mapper=mapper)
        wf = _make_workflow([
            WorkflowPhase(index=0, name="draft"),
            WorkflowPhase(index=1, name="review"),
        ])
        valid, errors, _ = validator.validate_workflow(wf)
        assert valid is False
        assert any("incompatible types" in e for e in errors)

    def test_mapper_raises_unknown_exception_becomes_warning(self):
        draft = _make_phase_def("draft", outputs=["content"])
        review = _make_phase_def("review", inputs={"content": False})
        registry = StubRegistry({"draft": draft, "review": review})
        mapper = StubMapper(raise_error=RuntimeError("unexpected"))
        validator = WorkflowValidator(registry=registry, mapper=mapper)
        wf = _make_workflow([
            WorkflowPhase(index=0, name="draft"),
            WorkflowPhase(index=1, name="review"),
        ])
        valid, errors, warnings = validator.validate_workflow(wf)
        # Unexpected mapper failures degrade to warnings (don't fail validation)
        assert any("unexpected" in w for w in warnings)


# ---------------------------------------------------------------------------
# validate_workflow — timeout / retries warnings
# ---------------------------------------------------------------------------


class TestTimeoutAndRetryWarnings:
    def test_short_timeout_warns(self):
        # PhaseDefinition allows any timeout, so we can stub it directly
        draft = _make_phase_def("draft", timeout=5)
        registry = StubRegistry({"draft": draft})
        validator = WorkflowValidator(registry=registry, mapper=StubMapper())
        wf = _make_workflow([WorkflowPhase(index=0, name="draft")])
        valid, _, warnings = validator.validate_workflow(wf)
        assert valid is True
        assert any("too short" in w for w in warnings)

    def test_high_retries_warns(self):
        draft = _make_phase_def("draft", retries=8)
        registry = StubRegistry({"draft": draft})
        validator = WorkflowValidator(registry=registry, mapper=StubMapper())
        wf = _make_workflow([WorkflowPhase(index=0, name="draft")])
        valid, _, warnings = validator.validate_workflow(wf)
        assert valid is True
        assert any("max_retries is high" in w for w in warnings)

    def test_normal_timeout_and_retries_no_warning(self):
        draft = _make_phase_def("draft", timeout=300, retries=3)
        registry = StubRegistry({"draft": draft})
        validator = WorkflowValidator(registry=registry, mapper=StubMapper())
        wf = _make_workflow([WorkflowPhase(index=0, name="draft")])
        valid, _, warnings = validator.validate_workflow(wf)
        assert valid is True
        assert not any("too short" in w or "max_retries" in w for w in warnings)


# ---------------------------------------------------------------------------
# validate_for_execution
# ---------------------------------------------------------------------------


class TestValidateForExecution:
    def test_validation_failure_propagates(self):
        validator = WorkflowValidator(registry=StubRegistry({}), mapper=StubMapper())
        wf = _make_workflow([WorkflowPhase(index=0, name="missing")])
        ok, errors = validator.validate_for_execution(wf)
        assert ok is False
        assert errors  # at least one error message

    def test_first_phase_required_input_missing_is_error(self):
        draft = _make_phase_def("draft", inputs={"topic": True})
        registry = StubRegistry({"draft": draft})
        validator = WorkflowValidator(registry=registry, mapper=StubMapper())
        wf = _make_workflow([WorkflowPhase(index=0, name="draft", user_inputs={})])
        ok, errors = validator.validate_for_execution(wf, initial_inputs={})
        assert ok is False
        assert any("topic" in e for e in errors)

    def test_first_phase_required_input_satisfied_by_user_inputs(self):
        draft = _make_phase_def("draft", inputs={"topic": True})
        registry = StubRegistry({"draft": draft})
        validator = WorkflowValidator(registry=registry, mapper=StubMapper())
        wf = _make_workflow([
            WorkflowPhase(index=0, name="draft", user_inputs={"topic": "AI"}),
        ])
        ok, errors = validator.validate_for_execution(wf)
        assert ok is True
        assert errors == []

    def test_first_phase_required_input_satisfied_by_initial_inputs(self):
        draft = _make_phase_def("draft", inputs={"topic": True})
        registry = StubRegistry({"draft": draft})
        validator = WorkflowValidator(registry=registry, mapper=StubMapper())
        wf = _make_workflow([WorkflowPhase(index=0, name="draft")])
        ok, errors = validator.validate_for_execution(wf, initial_inputs={"topic": "x"})
        assert ok is True


# ---------------------------------------------------------------------------
# _normalize_phases
# ---------------------------------------------------------------------------


class TestNormalizePhases:
    def test_passes_workflow_phase_through(self):
        validator = WorkflowValidator(registry=StubRegistry({}), mapper=StubMapper())
        phase = WorkflowPhase(index=0, name="draft")
        result = validator._normalize_phases([phase])
        assert len(result) == 1
        assert result[0] is phase

    def test_converts_dict_to_workflow_phase(self):
        validator = WorkflowValidator(registry=StubRegistry({}), mapper=StubMapper())
        result = validator._normalize_phases([
            {"index": 0, "name": "draft", "user_inputs": {"k": "v"}},
        ])
        assert len(result) == 1
        assert isinstance(result[0], WorkflowPhase)
        assert result[0].name == "draft"
        assert result[0].user_inputs == {"k": "v"}

    def test_extracts_from_object_with_name(self):
        validator = WorkflowValidator(registry=StubRegistry({}), mapper=StubMapper())

        class FakePhaseConfig:
            name = "extracted"
            metadata = {"foo": "bar"}

        result = validator._normalize_phases([FakePhaseConfig()])
        assert len(result) == 1
        assert result[0].name == "extracted"
        assert result[0].user_inputs == {"foo": "bar"}

    def test_unknown_type_is_skipped(self):
        validator = WorkflowValidator(registry=StubRegistry({}), mapper=StubMapper())
        # Object with no .name attribute and not a dict
        result = validator._normalize_phases([12345])
        assert result == []


# ---------------------------------------------------------------------------
# Default constructor pulls singleton (smoke test)
# ---------------------------------------------------------------------------


class TestDefaultConstructor:
    def test_default_init_uses_singleton(self):
        # No args — should not raise. The real PhaseRegistry singleton has
        # phases registered at import time.
        validator = WorkflowValidator()
        assert validator.registry is not None
        assert validator.mapper is not None
