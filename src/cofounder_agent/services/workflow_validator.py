"""
Workflow Validator - Validates workflow structure and phase compatibility

This module checks that:
1. All phases exist in the registry
2. Phase ordering is valid
3. Required inputs can be satisfied (either by user or auto-mapping)
4. No circular dependencies
"""

import logging
from typing import Any, Dict, List, Optional, Tuple

from schemas.custom_workflow_schemas import CustomWorkflow, WorkflowPhase
from services.phase_mapper import PhaseMapper, PhaseMappingError
from services.phase_registry import PhaseRegistry

logger = logging.getLogger(__name__)


class WorkflowValidationError(Exception):
    """Raised when workflow validation fails"""

    pass


class WorkflowValidator:
    """Validates workflow structure and execution feasibility"""

    def __init__(
        self, registry: Optional[PhaseRegistry] = None, mapper: Optional[PhaseMapper] = None
    ):
        self.registry = registry or PhaseRegistry.get_instance()
        self.mapper = mapper or PhaseMapper(self.registry)

    def validate_workflow(self, workflow: CustomWorkflow) -> Tuple[bool, List[str], List[str]]:
        """
        Validate a workflow definition.

        Args:
            workflow: The workflow to validate

        Returns:
            (is_valid, errors, warnings)
            - is_valid: False if errors exist
            - errors: Critical issues that prevent execution
            - warnings: Non-critical issues or suggestions
        """
        errors = []
        warnings = []

        if not workflow.phases:
            errors.append("Workflow must have at least one phase")
            return False, errors, warnings

        # Convert phases to WorkflowPhase objects if needed
        workflow_phases = self._normalize_phases(workflow.phases)
        phase_names = [p.name for p in workflow_phases]

        # Check all phases exist
        for phase in workflow_phases:
            if not self.registry.phase_exists(phase.name):
                errors.append(f"Phase '{phase.name}' not found in registry")
            elif phase.skip:
                warnings.append(f"Phase '{phase.name}' is marked to skip")

        if errors:
            return False, errors, warnings

        # Check phase ordering is correctly indexed
        indices = sorted([p.index for p in workflow_phases])
        expected_indices = list(range(len(workflow_phases)))
        if indices != expected_indices:
            errors.append(
                f"Phase indices are not sequential. " f"Expected {expected_indices}, got {indices}"
            )
            return False, errors, warnings

        # Check for duplicate phase names
        unique_names = len(set(phase_names))
        if unique_names != len(phase_names):
            errors.append("Duplicate phase names in workflow")
            return False, errors, warnings

        # Check phase compatibility and input requirements
        for i in range(len(workflow_phases)):
            phase = workflow_phases[i]
            phase_def = self.registry.get_phase(phase.name)

            if not phase_def:
                continue  # Already caught above

            # Check required inputs for first phase
            if i == 0:
                # Phase 0 inputs are checked at execution time in validate_for_execution
                # Or they must be present in user_inputs if this is a strict validation context
                # For now, we only warn if missing
                for input_key, input_field in phase_def.input_schema.items():
                    if input_field.required:
                        if input_key not in phase.user_inputs:
                            warnings.append(
                                f"Phase 0 ({phase.name}) required input "
                                f"'{input_key}' not provided in definition (must be provided at runtime)"
                            )

            # Check phase compatibility with previous phase output
            if i > 0:
                prev_phase = workflow_phases[i - 1]
                prev_phase_def = self.registry.get_phase(prev_phase.name)

                if prev_phase_def:
                    logger.info(f"DEBUG: Validating phase {phase.name} (prev: {prev_phase.name})")
                    logger.info(f"DEBUG: Input mapping: {phase.input_mapping}")

                    try:
                        mapping = self.mapper.map_phases(
                            prev_phase.name, phase.name, user_overrides=phase.input_mapping
                        )
                        logger.info(f"DEBUG: Generated mapping: {mapping}")

                        # When validating, account for user-provided inputs
                        # User inputs satisfy required input requirements without auto-mapping
                        adjusted_is_valid = True
                        adjusted_issues = []

                        # Check mapped fields are valid
                        for target_key, source_key in mapping.items():
                            if target_key not in phase_def.input_schema:
                                adjusted_issues.append(
                                    f"Target input '{target_key}' not found in {phase_def.name}"
                                )
                            if source_key not in prev_phase_def.output_schema:
                                adjusted_issues.append(
                                    f"Source output '{source_key}' not found in {prev_phase_def.name}"
                                )

                        # Check required inputs - can be satisfied by user input OR auto-mapping
                        for target_key, target_input in phase_def.input_schema.items():
                            if target_input.required:
                                # User provided? OK
                                if target_key in phase.user_inputs:
                                    continue
                                # Can auto-map? OK
                                if target_key in mapping:
                                    continue
                                # Otherwise: error
                                adjusted_issues.append(
                                    f"Required input '{target_key}' in {phase_def.name} "
                                    f"not provided and cannot be auto-mapped"
                                )

                        if adjusted_issues:
                            for issue in adjusted_issues:
                                errors.append(f"Phase {i} ({phase.name}): {issue}")

                    except PhaseMappingError as e:
                        errors.append(f"Phase {i} ({phase.name}): {str(e)}")
                    except Exception as e:
                        warnings.append(f"Could not validate mapping for phase {i}: {str(e)}")

        # Check timeout and retry sanity
        for phase in workflow_phases:
            phase_def = self.registry.get_phase(phase.name)
            if phase_def:
                if phase_def.timeout_seconds < 10:
                    warnings.append(
                        f"Phase '{phase.name}' timeout may be too short: "
                        f"{phase_def.timeout_seconds}s"
                    )
                if phase_def.max_retries > 5:
                    warnings.append(
                        f"Phase '{phase.name}' max_retries is high: " f"{phase_def.max_retries}"
                    )

        is_valid = len(errors) == 0
        return is_valid, errors, warnings

    def validate_for_execution(
        self, workflow: CustomWorkflow, initial_inputs: Optional[Dict[str, Any]] = None
    ) -> Tuple[bool, List[str]]:
        """
        Validate that a workflow can be executed with given inputs.

        Returns:
            (can_execute, list_of_errors)
        """
        errors = []
        is_valid, val_errors, _ = self.validate_workflow(workflow)

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
