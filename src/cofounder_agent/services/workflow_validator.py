"""
Workflow Validator - Validates workflow structure and phase compatibility

This module checks that:
1. All phases exist in the registry
2. Phase ordering is valid
3. Required inputs can be satisfied (either by user or auto-mapping)
4. No circular dependencies
"""

from typing import Any, Dict, List, Optional, Tuple

from schemas.custom_workflow_schemas import CustomWorkflow, WorkflowPhase
from services.logger_config import get_logger
from services.phase_mapper import PhaseMapper, PhaseMappingError
from services.phase_registry import PhaseRegistry

logger = get_logger(__name__)


class WorkflowValidationError(Exception):
    """Raised when workflow validation fails"""


class WorkflowValidator:
    """Validates workflow structure and execution feasibility"""

    def __init__(
        self, registry: Optional[PhaseRegistry] = None, mapper: Optional[PhaseMapper] = None
    ):
        self.registry = registry or PhaseRegistry.get_instance()
        self.mapper = mapper or PhaseMapper(self.registry)

    def validate_workflow(
        self, workflow: CustomWorkflow, initial_inputs: Optional[Dict[str, Any]] = None
    ) -> Tuple[bool, List[str], List[str]]:
        """
        Validate a workflow definition.

        Delegates each concern to a focused sub-validator and aggregates results.

        Args:
            workflow: The workflow to validate
            initial_inputs: Optional initial input values (used for execution-time validation)

        Returns:
            (is_valid, errors, warnings)
            - is_valid: False if errors exist
            - errors: Critical issues that prevent execution
            - warnings: Non-critical issues or suggestions
        """
        errors: List[str] = []
        warnings: List[str] = []

        if not workflow.phases:
            errors.append("Workflow must have at least one phase")
            return False, errors, warnings

        workflow_phases = self._normalize_phases(workflow.phases)

        # Each sub-validator returns (errors, warnings) for its concern
        phase_errors, phase_warnings = self._validate_phase_registry(workflow_phases)
        errors.extend(phase_errors)
        warnings.extend(phase_warnings)
        if errors:
            return False, errors, warnings

        index_errors, _ = self._validate_phase_indices(workflow_phases)
        errors.extend(index_errors)
        if errors:
            return False, errors, warnings

        dup_errors, _ = self._validate_no_duplicate_names(workflow_phases)
        errors.extend(dup_errors)
        if errors:
            return False, errors, warnings

        mapping_errors, mapping_warnings = self._validate_phase_mappings(
            workflow_phases, initial_inputs
        )
        errors.extend(mapping_errors)
        warnings.extend(mapping_warnings)

        timeout_errors, timeout_warnings = self._validate_timeout_and_retries(workflow_phases)
        errors.extend(timeout_errors)
        warnings.extend(timeout_warnings)

        return len(errors) == 0, errors, warnings

    # ---------------------------------------------------------------------------
    # Sub-validators — each covers one validation concern
    # ---------------------------------------------------------------------------

    def _validate_phase_registry(
        self, workflow_phases: List[WorkflowPhase]
    ) -> Tuple[List[str], List[str]]:
        """Check that all phases exist in the registry and flag skipped phases."""
        errors: List[str] = []
        warnings: List[str] = []
        for phase in workflow_phases:
            if not self.registry.phase_exists(phase.name):
                errors.append(f"Phase '{phase.name}' not found in registry")
            elif phase.skip:
                warnings.append(f"Phase '{phase.name}' is marked to skip")
        return errors, warnings

    def _validate_phase_indices(
        self, workflow_phases: List[WorkflowPhase]
    ) -> Tuple[List[str], List[str]]:
        """Check that phase indices are sequential starting from zero."""
        errors: List[str] = []
        indices = sorted([p.index for p in workflow_phases])
        expected = list(range(len(workflow_phases)))
        if indices != expected:
            errors.append(f"Phase indices are not sequential. Expected {expected}, got {indices}")
        return errors, []

    def _validate_no_duplicate_names(
        self, workflow_phases: List[WorkflowPhase]
    ) -> Tuple[List[str], List[str]]:
        """Check that no two phases share the same name."""
        phase_names = [p.name for p in workflow_phases]
        if len(set(phase_names)) != len(phase_names):
            return ["Duplicate phase names in workflow"], []
        return [], []

    def _validate_phase_mappings(
        self,
        workflow_phases: List[WorkflowPhase],
        initial_inputs: Optional[Dict[str, Any]],
    ) -> Tuple[List[str], List[str]]:
        """Validate input/output mappings between consecutive phases."""
        errors: List[str] = []
        warnings: List[str] = []

        for i, phase in enumerate(workflow_phases):
            phase_def = self.registry.get_phase(phase.name)
            if not phase_def:
                continue  # Already caught by _validate_phase_registry

            if i == 0:
                # Phase 0: required inputs must be provided at runtime — only warn
                for input_key, input_field in phase_def.input_schema.items():
                    if input_field.required and input_key not in phase.user_inputs:
                        warnings.append(
                            f"Phase 0 ({phase.name}) required input '{input_key}' "
                            f"not provided in definition (must be provided at runtime)"
                        )
                continue

            # Phases 1+: validate mapping from previous phase output
            prev_phase = workflow_phases[i - 1]
            prev_phase_def = self.registry.get_phase(prev_phase.name)
            if not prev_phase_def:
                continue

            logger.debug("Validating phase %s (prev: %s)", phase.name, prev_phase.name)
            phase_errors, phase_warnings = self._validate_single_phase_mapping(
                phase_index=i,
                phase=phase,
                phase_def=phase_def,
                prev_phase=prev_phase,
                prev_phase_def=prev_phase_def,
                initial_inputs=initial_inputs,
            )
            errors.extend(phase_errors)
            warnings.extend(phase_warnings)

        return errors, warnings

    def _validate_single_phase_mapping(
        self,
        phase_index: int,
        phase: WorkflowPhase,
        phase_def: Any,
        prev_phase: WorkflowPhase,
        prev_phase_def: Any,
        initial_inputs: Optional[Dict[str, Any]],
    ) -> Tuple[List[str], List[str]]:
        """Validate that one phase can receive inputs from the previous phase."""
        errors: List[str] = []
        warnings: List[str] = []
        prefix = f"Phase {phase_index} ({phase.name})"

        try:
            mapping = self.mapper.map_phases(
                prev_phase.name, phase.name, user_overrides=phase.input_mapping
            )
            logger.debug("Generated mapping: %s", mapping)

            # Check mapped field names are valid in both schemas
            for target_key, source_key in mapping.items():
                if target_key not in phase_def.input_schema:
                    errors.append(
                        f"{prefix}: Target input '{target_key}' not found in {phase_def.name}"
                    )
                if source_key not in prev_phase_def.output_schema:
                    errors.append(
                        f"{prefix}: Source output '{source_key}' not found in {prev_phase_def.name}"
                    )

            # Check all required inputs can be satisfied
            for target_key, target_input in phase_def.input_schema.items():
                if not target_input.required:
                    continue
                satisfied = (
                    target_key in phase.user_inputs
                    or (initial_inputs is not None and target_key in initial_inputs)
                    or target_key in mapping
                )
                if not satisfied:
                    errors.append(
                        f"{prefix}: Required input '{target_key}' in {phase_def.name} "
                        f"not provided and cannot be auto-mapped"
                    )

        except PhaseMappingError as e:
            errors.append(f"{prefix}: {str(e)}")
        except Exception as e:
            logger.error(
                "[validate_workflow] Could not validate mapping for phase_index=%d, phase_name=%r: %s",
                phase_index,
                phase.name,
                str(e),
                exc_info=True,
            )
            warnings.append(f"Could not validate mapping for phase {phase_index}: {str(e)}")

        return errors, warnings

    def _validate_timeout_and_retries(
        self, workflow_phases: List[WorkflowPhase]
    ) -> Tuple[List[str], List[str]]:
        """Warn on phases with suspiciously short timeouts or high retry counts."""
        warnings: List[str] = []
        for phase in workflow_phases:
            phase_def = self.registry.get_phase(phase.name)
            if not phase_def:
                continue
            if phase_def.timeout_seconds < 10:
                warnings.append(
                    f"Phase '{phase.name}' timeout may be too short: {phase_def.timeout_seconds}s"
                )
            if phase_def.max_retries > 5:
                warnings.append(
                    f"Phase '{phase.name}' max_retries is high: {phase_def.max_retries}"
                )
        return [], warnings

    def validate_for_execution(
        self, workflow: CustomWorkflow, initial_inputs: Optional[Dict[str, Any]] = None
    ) -> Tuple[bool, List[str]]:
        """
        Validate that a workflow can be executed with given inputs.

        Returns:
            (can_execute, list_of_errors)
        """
        errors = []
        is_valid, val_errors, _ = self.validate_workflow(workflow, initial_inputs=initial_inputs)

        if not is_valid:
            return False, val_errors

        # Check that all required inputs for first phase are satisfied
        workflow_phases = self._normalize_phases(workflow.phases)
        if workflow_phases:
            first_phase = workflow_phases[0]
            first_phase_def = self.registry.get_phase(first_phase.name)

            if first_phase_def:
                for input_key, input_field in first_phase_def.input_schema.items():
                    if input_field.required:
                        has_default = input_field.default_value is not None
                        has_user_input = input_key in first_phase.user_inputs
                        has_initial = initial_inputs and input_key in initial_inputs

                        if not (has_default or has_user_input or has_initial):
                            errors.append(
                                f"Required input '{input_key}' for first phase "
                                f"'{first_phase.name}' not provided"
                            )

        return len(errors) == 0, errors

    def _normalize_phases(self, phases: List[Any]) -> List[WorkflowPhase]:
        """
        Convert phases to WorkflowPhase objects if needed.
        Handles both WorkflowPhase objects and dict representations.
        """
        normalized = []

        for phase in phases:
            if isinstance(phase, WorkflowPhase):
                normalized.append(phase)
            elif isinstance(phase, dict):
                # Convert dict to WorkflowPhase
                normalized.append(WorkflowPhase(**phase))
            else:
                # Try to extract phase name if it's a different type
                if hasattr(phase, "name"):
                    # Assume it's a PhaseConfig or similar
                    normalized.append(
                        WorkflowPhase(
                            index=len(normalized),
                            name=phase.name,
                            user_inputs=getattr(phase, "metadata", {}),
                            model_overrides=None,
                            skip=False,
                        )
                    )
                else:
                    logger.warning(f"Could not normalize phase: {phase}")

        return normalized
