"""
Workflow Executor - Executes workflows with proper phase sequencing and data flow

This module handles:
1. Sequential phase execution in order
2. Automatic output-to-input mapping between phases
3. Input tracing (tracking where data came from)
4. Error handling and recovery
5. Progress tracking
"""

import time
from typing import Any

from schemas.custom_workflow_schemas import CustomWorkflow, InputTrace, PhaseResult, WorkflowPhase
from services.logger_config import get_logger
from services.phase_mapper import PhaseMapper, PhaseMappingError, build_full_phase_pipeline
from services.phase_registry import PhaseRegistry

logger = get_logger(__name__)


class WorkflowExecutionError(Exception):
    """Raised when workflow execution fails"""


class WorkflowExecutor:
    """
    Executes workflows with proper phase sequencing and data flow.

    Workflow execution flow:
    1. Sort phases by index
    2. Apply phase mapper to generate input mappings
    3. For each phase:
        a. Prepare inputs (from previous outputs + user inputs)
        b. Execute phase
        c. Capture result with input trace
    4. Return all results with lineage
    """

    # Class-level cache for imported agent modules — avoids re-importing on every phase
    _agent_module_cache: dict[str, Any] = {}

    def __init__(
        self, registry: PhaseRegistry | None = None, mapper: PhaseMapper | None = None
    ):
        self.registry = registry or PhaseRegistry.get_instance()
        self.mapper = mapper or PhaseMapper(self.registry)

    async def execute_workflow(
        self,
        workflow: CustomWorkflow,
        initial_inputs: dict[str, Any] | None = None,
        execution_id: str | None = None,
        progress_service: Any | None = None,
    ) -> dict[str, PhaseResult]:
        """
        Execute a workflow and return results from all phases.

        Args:
            workflow: The workflow to execute
            initial_inputs: Initial input values for the first phase
            execution_id: Optional ID for tracking this execution
            progress_service: Optional progress service for real-time tracking

        Returns:
            Dictionary mapping phase names to their results:
            {
                "research": PhaseResult(...),
                "draft": PhaseResult(...),
                ...
            }

        Raises:
            WorkflowExecutionError: If execution fails
        """
        if execution_id is None:
            execution_id = f"exec-{int(time.time() * 1000)}"

        logger.info("Starting workflow execution: %s", execution_id)
        logger.info("Workflow: %s (%d phases)", workflow.name, len(workflow.phases))

        # Normalize phases to WorkflowPhase objects
        phases = self._normalize_phases(workflow.phases)

        # Sort phases by index (should already be sorted, but ensure it)
        phases.sort(key=lambda p: p.index)

        # Build phase mapping
        phase_names = [p.name for p in phases]
        try:
            phase_mappings = build_full_phase_pipeline(phase_names)
        except PhaseMappingError as e:
            raise WorkflowExecutionError(f"Failed to build phase pipeline: {str(e)}") from e

        # Initialize results storage
        phase_results: dict[str, PhaseResult] = {}
        phase_outputs: dict[str, dict[str, Any]] = {}  # For passing data between phases

        # Prepare initial input for first phase
        first_phase_inputs = dict(initial_inputs or {})

        try:
            # Execute phases in order
            for i, phase in enumerate(phases):
                if phase.skip:
                    logger.info("Skipping phase %d: %s", i, phase.name)
                    # Update progress for skipped phase
                    if progress_service:
                        try:
                            progress_service.complete_phase(
                                execution_id=execution_id,
                                phase_name=phase.name,
                                phase_output=None,
                                duration_ms=0,
                            )
                        except Exception as e:
                            logger.error(
                                "[_execute_workflow] Failed to update progress for skipped phase: %s",
                                e, exc_info=True,
                            )
                    continue

                logger.info("Executing phase %d: %s", i, phase.name)
                start_time = time.time()

                # Update progress: start phase
                if progress_service:
                    try:
                        progress_service.start_phase(
                            execution_id=execution_id,
                            phase_index=i,
                            phase_name=phase.name,
                        )
                    except Exception as e:
                        logger.error(
                            "[_execute_workflow] Failed to update progress for phase start: %s",
                            e, exc_info=True,
                        )

                # Prepare inputs for this phase
                # Pass initial_inputs to ALL phases as fallback (topic, style, tone, etc.
                # should be available throughout the pipeline)
                phase_inputs, input_traces = self._prepare_phase_inputs(
                    phase,
                    i,
                    first_phase_inputs,
                    phase_outputs,
                    phase_mappings.get(phase.name, {}),
                )

                # Execute the phase
                try:
                    result = await self._execute_phase(phase, phase_inputs, execution_id)
                except Exception as e:
                    logger.error(
                        "[_execute_workflow] Phase %d (%s) failed: %s",
                        i, phase.name, e, exc_info=True,
                    )
                    result = PhaseResult(
                        status="failed",
                        error=str(e),
                        execution_time_ms=(time.time() - start_time) * 1000,
                        model_used=None,
                        tokens_used=None,
                    )

                result.execution_time_ms = (time.time() - start_time) * 1000
                result.input_trace = input_traces

                # Update progress: phase complete or failed
                if progress_service:
                    try:
                        if result.status == "completed":
                            progress_service.complete_phase(
                                execution_id=execution_id,
                                phase_name=phase.name,
                                phase_output=result.output,
                                duration_ms=result.execution_time_ms,
                            )
                        else:
                            progress_service.fail_phase(
                                execution_id=execution_id,
                                phase_name=phase.name,
                                error=result.error or "Unknown error",
                            )
                    except Exception as e:
                        logger.error(
                            "[_execute_workflow] Failed to update progress for phase completion: %s",
                            e, exc_info=True,
                        )

                # Store result
                phase_results[phase.name] = result

                # Store output for next phase
                phase_outputs[phase.name] = result.output

                # Log progress
                logger.info(
                    "Phase %d (%s) status: %s (time: %.0fms)",
                    i, phase.name, result.status, result.execution_time_ms,
                )

                # Stop on failure (for now, can be made configurable)
                if result.status == "failed":
                    logger.warning("Phase %d (%s) failed, halting workflow", i, phase.name)
                    raise WorkflowExecutionError(
                        f"Phase {phase.name} (index {i}) failed: {result.error}"
                    )

        except Exception as e:
            logger.error("[_execute_workflow] Workflow execution failed: %s", e, exc_info=True)
            # Mark remaining phases as not executed
            for phase in phases[len(phase_results) :]:
                if phase.name not in phase_results:
                    phase_results[phase.name] = PhaseResult(
                        status="skipped",
                        error="Workflow execution halted",
                        execution_time_ms=0.0,
                        model_used=None,
                        tokens_used=None,
                    )

        logger.info("Workflow execution complete: %d phases executed", len(phase_results))
        return phase_results

    def _prepare_phase_inputs(
        self,
        phase: WorkflowPhase,
        phase_index: int,
        initial_inputs: dict[str, Any],
        previous_outputs: dict[str, dict[str, Any]],
        phase_mapping: dict[str, str],
    ) -> tuple[dict[str, Any], dict[str, InputTrace]]:
        """
        Prepare input dict for a phase, merging auto-mapped inputs and user inputs.

        Returns:
            (inputs_dict, input_trace_dict)
        """
        inputs = {}
        traces = {}

        # Strategy 1: Add user-provided inputs (highest priority)
        for key, value in phase.user_inputs.items():
            inputs[key] = value
            traces[key] = InputTrace(
                source_phase=None, source_field=None, user_provided=True, auto_mapped=False
            )

        # Strategy 2: Add auto-mapped inputs from previous phase
        if phase_index > 0:
            for target_key, source_key in phase_mapping.items():
                if target_key not in inputs:  # Don't override user inputs
                    # Extract source output field from previous phase
                    prev_phase_name = (
                        list(previous_outputs.keys())[-1] if previous_outputs else None
                    )
                    if prev_phase_name and source_key in previous_outputs.get(prev_phase_name, {}):
                        source_value = previous_outputs[prev_phase_name][source_key]
                        inputs[target_key] = source_value
                        traces[target_key] = InputTrace(
                            source_phase=prev_phase_name,
                            source_field=source_key,
                            user_provided=False,
                            auto_mapped=True,
                        )

        # Strategy 2.5: Carry forward all previous phase outputs as fallback
        # (e.g., draft outputs "content" which assess needs as input)
        if phase_index > 0:
            for prev_name in reversed(list(previous_outputs.keys())):
                for key, value in previous_outputs[prev_name].items():
                    if key not in inputs:
                        inputs[key] = value
                        traces[key] = InputTrace(
                            source_phase=prev_name,
                            source_field=key,
                            user_provided=False,
                            auto_mapped=True,
                        )

        # Strategy 3: Add initial inputs as fallback for all phases
        # (topic, style, tone, target_length should be available throughout pipeline)
        for key, value in initial_inputs.items():
            if key not in inputs:
                inputs[key] = value
                traces[key] = InputTrace(
                    source_phase=None, source_field=None, user_provided=True, auto_mapped=False
                )

        # Strategy 4: Fill defaults from phase definition
        phase_def = self.registry.get_phase(phase.name)
        if phase_def:
            for input_key, input_field in phase_def.input_schema.items():
                if input_key not in inputs and input_field.default_value is not None:
                    inputs[input_key] = input_field.default_value
                    traces[input_key] = InputTrace(
                        source_phase=None, source_field=None, user_provided=False, auto_mapped=False
                    )

        return inputs, traces

    async def _execute_phase(
        self, phase: WorkflowPhase, inputs: dict[str, Any], execution_id: str
    ) -> PhaseResult:
        """
        Execute a single phase and return the result.

        Delegates to agents based on phase definition's agent_type.
        """
        phase_def = self.registry.get_phase(phase.name)
        if not phase_def:
            return PhaseResult(
                status="failed",
                output={},
                error=f"Phase '{phase.name}' not found in registry",
                execution_time_ms=0.0,
                model_used=None,
                tokens_used=None,
            )

        try:
            import asyncio

            logger.info("Executing %s with %d inputs", phase.name, len(inputs))
            logger.debug("Agent type: %s", phase_def.agent_type)

            # Get the agent based on agent_type
            agent = self._get_agent(phase_def.agent_type)
            if not agent:
                return PhaseResult(
                    status="failed",
                    output={},
                    error=f"Agent '{phase_def.agent_type}' not found",
                    execution_time_ms=0.0,
                    model_used=None,
                    tokens_used=None,
                )

            # Run the agent (handle both sync and async agents)
            start_time = time.time()
            if hasattr(agent, "run") and callable(agent.run):
                if asyncio.iscoroutinefunction(agent.run):
                    result_output = await agent.run(inputs)
                else:
                    result_output = agent.run(inputs)
            else:
                return PhaseResult(
                    status="failed",
                    output={},
                    error=f"Agent '{phase_def.agent_type}' has no run method",
                    execution_time_ms=0.0,
                    model_used=None,
                    tokens_used=None,
                )

            execution_time_ms = (time.time() - start_time) * 1000

            # Extract status from agent output
            agent_status = result_output.get("status", "unknown")  # type: ignore[union-attr]

            # Check for errors in agent output
            if agent_status == "failed":
                return PhaseResult(
                    status="failed",
                    output=result_output,  # type: ignore[arg-type]
                    error=result_output.get("error", "Agent execution failed"),  # type: ignore[union-attr]
                    execution_time_ms=execution_time_ms,
                    model_used=phase_def.agent_type,
                    tokens_used=None,
                )

            return PhaseResult(
                status="completed" if agent_status == "success" else "failed",
                output=result_output,  # type: ignore[arg-type]
                error=None,
                execution_time_ms=execution_time_ms,
                model_used=phase_def.agent_type,
                tokens_used=None,
            )

        except Exception as e:
            logger.error(
                "[_execute_phase] Failed to execute %s: %s", phase.name, e, exc_info=True
            )
            return PhaseResult(
                status="failed",
                output={},
                error=str(e),
                execution_time_ms=0.0,
                model_used=None,
                tokens_used=None,
            )

    def _get_agent(self, agent_type: str) -> Any | None:
        """
        Get an agent instance by agent_type string.

        Supports blog agents and other agent types.
        """
        try:
            # Map agent_type to import path and factory function
            agent_mapping = {
                "blog_content_generator_agent": (
                    "agents.blog_content_generator_agent",
                    "get_blog_content_generator_agent",
                ),
                "blog_quality_agent": ("agents.blog_quality_agent", "get_blog_quality_agent"),
                "blog_image_agent": ("agents.blog_image_agent", "get_blog_image_agent"),
                "blog_publisher_agent": ("agents.blog_publisher_agent", "get_blog_publisher_agent"),
                "research_agent": (
                    "agents.content_agent.agents.research_agent",
                    "get_research_agent",
                ),
                "creative_agent": (
                    "agents.content_agent.agents.creative_agent",
                    "get_creative_agent",
                ),
                "qa_agent": ("agents.content_agent.agents.qa_agent", "get_qa_agent"),
                "image_agent": (
                    "agents.blog_image_agent",
                    "get_blog_image_agent",
                ),
                "publishing_agent": (
                    "agents.blog_publisher_agent",
                    "get_blog_publisher_agent",
                ),
            }

            if agent_type not in agent_mapping:
                logger.warning("Agent type '%s' not in mapping", agent_type)
                return None

            module_path, factory_func = agent_mapping[agent_type]

            try:
                # Check class-level cache before importing
                if module_path not in WorkflowExecutor._agent_module_cache:
                    import importlib

                    WorkflowExecutor._agent_module_cache[module_path] = importlib.import_module(
                        module_path
                    )
                    logger.debug("Imported and cached agent module '%s'", module_path)

                module = WorkflowExecutor._agent_module_cache[module_path]

                # Get the factory function
                if hasattr(module, factory_func):
                    agent_factory = getattr(module, factory_func)
                    agent = agent_factory()
                    logger.debug("Loaded agent '%s' from %s", agent_type, module_path)
                    return agent
                else:
                    logger.warning("Factory function '%s' not found in %s", factory_func, module_path)
                    return None

            except ImportError as e:
                logger.error(
                    "[_get_agent] Failed to import agent module '%s': %s",
                    module_path, e, exc_info=True,
                )
                return None

        except Exception as e:
            logger.error("[_get_agent] Error loading agent '%s': %s", agent_type, e, exc_info=True)
            return None

    def _normalize_phases(self, phases: list[Any]) -> list[WorkflowPhase]:
        """Convert phases to WorkflowPhase objects"""
        normalized = []

        for i, phase in enumerate(phases):
            if isinstance(phase, WorkflowPhase):
                # Ensure index is set
                if phase.index is None:
                    phase.index = i
                normalized.append(phase)
            elif isinstance(phase, dict):
                # Dictionary representation
                if "index" not in phase:
                    phase["index"] = i
                normalized.append(WorkflowPhase(**phase))
            else:
                # Try to coerce to WorkflowPhase
                if hasattr(phase, "name"):
                    normalized.append(
                        WorkflowPhase(
                            index=i,
                            name=phase.name,
                            user_inputs=getattr(phase, "metadata", {}),
                            model_overrides=None,
                            skip=False,
                        )
                    )

        return normalized
