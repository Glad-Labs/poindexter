"""
Unit tests for WorkflowValidator sub-validators.

All tests use unittest.mock to avoid touching PhaseRegistry, PhaseMapper,
or any DB/network resources. Each sub-validator is tested independently.
"""

from typing import Dict, Optional
from unittest.mock import MagicMock

import pytest

from schemas.custom_workflow_schemas import WorkflowPhase
from services.workflow_validator import WorkflowValidator

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_phase(
    index: int, name: str, skip: bool = False, user_inputs: Optional[Dict] = None
) -> WorkflowPhase:
    return WorkflowPhase(  # type: ignore[call-arg]
        index=index,
        name=name,
        skip=skip,
        user_inputs=user_inputs or {},
        input_mapping={},
    )


def _make_phase_def(
    name: str,
    timeout_seconds: int = 300,
    max_retries: int = 3,
    input_schema: Optional[Dict] = None,
    output_schema: Optional[Dict] = None,
) -> MagicMock:
    phase_def = MagicMock()
    phase_def.name = name
    phase_def.timeout_seconds = timeout_seconds
    phase_def.max_retries = max_retries
    phase_def.input_schema = input_schema or {}
    phase_def.output_schema = output_schema or {}
    return phase_def


def _make_validator(
    registry: Optional[MagicMock] = None, mapper: Optional[MagicMock] = None
) -> WorkflowValidator:
    """Build a WorkflowValidator with mocked registry and mapper."""
    mock_registry = registry or MagicMock()
    mock_mapper = mapper or MagicMock()
    # Patch out the get_instance singleton call
    validator = WorkflowValidator.__new__(WorkflowValidator)
    validator.registry = mock_registry
    validator.mapper = mock_mapper
    return validator


# ---------------------------------------------------------------------------
# _validate_phase_registry
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestValidatePhaseRegistry:
    def test_all_phases_exist_returns_no_errors(self):
        registry = MagicMock()
        registry.phase_exists.return_value = True
        validator = _make_validator(registry=registry)

        phases = [_make_phase(0, "research"), _make_phase(1, "draft")]
        errors, warnings = validator._validate_phase_registry(phases)
        assert errors == []
        assert warnings == []

    def test_missing_phase_returns_error(self):
        registry = MagicMock()
        registry.phase_exists.side_effect = lambda name: name != "missing_phase"
        validator = _make_validator(registry=registry)

        phases = [_make_phase(0, "research"), _make_phase(1, "missing_phase")]
        errors, warnings = validator._validate_phase_registry(phases)
        assert any("missing_phase" in e for e in errors)
        assert warnings == []

    def test_skipped_phase_returns_warning_not_error(self):
        registry = MagicMock()
        registry.phase_exists.return_value = True
        validator = _make_validator(registry=registry)

        phases = [_make_phase(0, "research"), _make_phase(1, "draft", skip=True)]
        errors, warnings = validator._validate_phase_registry(phases)
        assert errors == []
        assert any("draft" in w for w in warnings)


# ---------------------------------------------------------------------------
# _validate_phase_indices
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestValidatePhaseIndices:
    def test_sequential_indices_are_valid(self):
        validator = _make_validator()
        phases = [_make_phase(0, "research"), _make_phase(1, "draft"), _make_phase(2, "refine")]
        errors, _ = validator._validate_phase_indices(phases)
        assert errors == []

    def test_non_sequential_indices_return_error(self):
        validator = _make_validator()
        phases = [_make_phase(0, "research"), _make_phase(2, "draft")]  # gap at index 1
        errors, _ = validator._validate_phase_indices(phases)
        assert len(errors) == 1
        assert "sequential" in errors[0]

    def test_single_phase_at_index_zero_is_valid(self):
        validator = _make_validator()
        phases = [_make_phase(0, "research")]
        errors, _ = validator._validate_phase_indices(phases)
        assert errors == []

    def test_non_zero_start_returns_error(self):
        validator = _make_validator()
        phases = [_make_phase(1, "research"), _make_phase(2, "draft")]  # starts at 1 not 0
        errors, _ = validator._validate_phase_indices(phases)
        assert len(errors) == 1


# ---------------------------------------------------------------------------
# _validate_no_duplicate_names
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestValidateNoDuplicateNames:
    def test_unique_names_are_valid(self):
        validator = _make_validator()
        phases = [_make_phase(0, "research"), _make_phase(1, "draft")]
        errors, _ = validator._validate_no_duplicate_names(phases)
        assert errors == []

    def test_duplicate_name_returns_error(self):
        validator = _make_validator()
        phases = [_make_phase(0, "research"), _make_phase(1, "research")]
        errors, _ = validator._validate_no_duplicate_names(phases)
        assert len(errors) == 1
        assert "Duplicate" in errors[0]


# ---------------------------------------------------------------------------
# _validate_timeout_and_retries
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestValidateTimeoutAndRetries:
    def test_normal_timeout_and_retries_no_warnings(self):
        registry = MagicMock()
        phase_def = _make_phase_def("research", timeout_seconds=300, max_retries=3)
        registry.get_phase.return_value = phase_def
        validator = _make_validator(registry=registry)

        phases = [_make_phase(0, "research")]
        errors, warnings = validator._validate_timeout_and_retries(phases)
        assert errors == []
        assert warnings == []

    def test_timeout_below_10_generates_warning(self):
        registry = MagicMock()
        phase_def = _make_phase_def("research", timeout_seconds=5, max_retries=3)
        registry.get_phase.return_value = phase_def
        validator = _make_validator(registry=registry)

        phases = [_make_phase(0, "research")]
        errors, warnings = validator._validate_timeout_and_retries(phases)
        assert errors == []
        assert any("timeout" in w.lower() for w in warnings)

    def test_max_retries_above_5_generates_warning(self):
        registry = MagicMock()
        phase_def = _make_phase_def("research", timeout_seconds=300, max_retries=6)
        registry.get_phase.return_value = phase_def
        validator = _make_validator(registry=registry)

        phases = [_make_phase(0, "research")]
        errors, warnings = validator._validate_timeout_and_retries(phases)
        assert errors == []
        assert any("max_retries" in w or "retries" in w.lower() for w in warnings)

    def test_missing_phase_def_is_skipped(self):
        registry = MagicMock()
        registry.get_phase.return_value = None
        validator = _make_validator(registry=registry)

        phases = [_make_phase(0, "unknown_phase")]
        errors, warnings = validator._validate_timeout_and_retries(phases)
        assert errors == []
        assert warnings == []


# ---------------------------------------------------------------------------
# validate_workflow — top-level integration
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestValidateWorkflow:
    def test_empty_phases_returns_error_immediately(self):
        validator = _make_validator()
        workflow = MagicMock()
        workflow.phases = []
        is_valid, errors, warnings = validator.validate_workflow(workflow)
        assert is_valid is False
        assert any("at least one phase" in e for e in errors)

    def test_valid_single_phase_workflow_passes(self):
        registry = MagicMock()
        phase_def = _make_phase_def(
            "research",
            timeout_seconds=300,
            max_retries=3,
            input_schema={"topic": MagicMock(required=True)},
        )
        registry.phase_exists.return_value = True
        registry.get_phase.return_value = phase_def
        validator = _make_validator(registry=registry)

        phase = _make_phase(0, "research", user_inputs={"topic": "AI"})
        workflow = MagicMock()
        workflow.phases = [phase]
        is_valid, errors, warnings = validator.validate_workflow(workflow)
        # Errors should be empty; phase 0 missing required inputs produce warnings only
        assert errors == []

    def test_unknown_phase_returns_false(self):
        registry = MagicMock()
        registry.phase_exists.return_value = False
        validator = _make_validator(registry=registry)

        phase = _make_phase(0, "ghost_phase")
        workflow = MagicMock()
        workflow.phases = [phase]
        is_valid, errors, _ = validator.validate_workflow(workflow)
        assert is_valid is False
        assert any("ghost_phase" in e for e in errors)
