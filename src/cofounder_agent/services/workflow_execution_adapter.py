"""
Workflow Execution Adapter

Bridges CustomWorkflow (user-created workflow definitions) with WorkflowEngine
execution model. Converts workflow phases to executable WorkflowPhase objects
with appropriate handlers.

Architecture:
1. Load CustomWorkflow from database
2. Convert phases to WorkflowPhase objects with handlers
3. Create WorkflowContext
4. Execute with WorkflowEngine
5. Track results in workflow_executions table
"""

import asyncio
import json
import logging
import re
import uuid
from datetime import datetime, timezone
from enum import Enum
from typing import Any, Callable, Dict, List, Optional

logger = logging.getLogger(__name__)


PHASE_TO_AGENT_MAP = {
    "research": "research_agent",
    "draft": "creative_agent",
    "refine": "creative_agent",
    "assess": "qa_agent",
    "image": "image_agent",
    "image_selection": "image_agent",
    "publish": "publishing_agent",
    "finalize": "publishing_agent",
}

CONTENT_PHASE_FALLBACK_TYPES = {
    "research",
    "draft",
    "assess",
    "refine",
    "image",
    "image_selection",
    "publish",
    "finalize",
}


def _normalize_phase_alias(value: Any) -> str:
    """Normalize phase/alias values for robust matching."""
    if not isinstance(value, str):
        return ""
    normalized = value.strip().lower().replace("-", " ").replace("_", " ")
    normalized = "_".join(normalized.split())
    normalized = re.sub(r"_\d+$", "", normalized)
    return normalized


def _is_resolvable_agent_name(agent_name: str) -> bool:
    """Check if a configured name already looks like a concrete agent id."""
    return bool(agent_name) and agent_name.endswith("_agent")


def resolve_phase_agent_name(
    configured_agent: Optional[str],
    phase_metadata: Optional[Dict[str, Any]] = None,
    phase_name: Optional[str] = None,
) -> str:
    """Resolve a phase configuration to a concrete agent id."""
    normalized_agent = _normalize_phase_alias(configured_agent)
    if _is_resolvable_agent_name(normalized_agent):
        return normalized_agent

    if normalized_agent in PHASE_TO_AGENT_MAP:
        return PHASE_TO_AGENT_MAP[normalized_agent]

    metadata = phase_metadata or {}
    metadata_phase_type = _normalize_phase_alias(metadata.get("phase_type"))
    if metadata_phase_type in PHASE_TO_AGENT_MAP:
        return PHASE_TO_AGENT_MAP[metadata_phase_type]

    normalized_phase_name = _normalize_phase_alias(phase_name)
    if normalized_phase_name in PHASE_TO_AGENT_MAP:
        return PHASE_TO_AGENT_MAP[normalized_phase_name]

    if normalized_agent:
        return normalized_agent

    return "creative_agent"


def _json_default_serializer(value: Any) -> Any:
    """Best-effort serializer for non-JSON-native types."""
    if isinstance(value, datetime):
        return value.isoformat()
    if isinstance(value, Enum):
        return value.value
    if hasattr(value, "model_dump") and callable(getattr(value, "model_dump")):
        return value.model_dump()
    if hasattr(value, "to_dict") and callable(getattr(value, "to_dict")):
        return value.to_dict()
    return str(value)


def _to_json_safe(value: Any) -> Any:
    """Convert arbitrary value to a JSON-safe structure."""
    if value is None:
        return None
    try:
        return json.loads(json.dumps(value, default=_json_default_serializer))
    except Exception:
        return str(value)


def _is_content_phase_for_fallback(
    phase_name: Optional[str],
    phase_metadata: Optional[Dict[str, Any]] = None,
) -> bool:
    """Determine if a phase should use content fallback execution."""
    normalized_phase_name = _normalize_phase_alias(phase_name)
    if normalized_phase_name in CONTENT_PHASE_FALLBACK_TYPES:
        return True

    metadata = phase_metadata or {}
    normalized_phase_type = _normalize_phase_alias(metadata.get("phase_type"))
    return normalized_phase_type in CONTENT_PHASE_FALLBACK_TYPES


def _build_content_fallback_prompt(phase_name: str, phase_input: Dict[str, Any]) -> str:
    """Build a deterministic fallback prompt for model-consolidation execution."""
    phase_instructions = {
        "research": "Gather concise factual research notes and key points.",
        "draft": "Produce a clear first draft suitable for publishing workflows.",
        "assess": "Evaluate quality, identify issues, and provide actionable improvement feedback.",
        "refine": "Improve and rewrite content based on available feedback and constraints.",
        "image": "Create image direction, prompt suggestions, and accessibility notes.",
        "image_selection": "Recommend image choices and rationale for the current content.",
        "publish": "Prepare final publish-ready package including title, summary, and metadata.",
        "finalize": "Prepare final publish-ready package including title, summary, and metadata.",
    }
    normalized_phase = _normalize_phase_alias(phase_name)
    phase_instruction = phase_instructions.get(
        normalized_phase,
        "Produce practical output for this workflow phase.",
    )

    safe_input = _to_json_safe(phase_input)
    serialized_input = (
        json.dumps(safe_input, sort_keys=True, ensure_ascii=False)
        if isinstance(safe_input, (dict, list))
        else str(safe_input)
    )
    return (
        f"You are executing workflow phase '{phase_name}'. "
        f"{phase_instruction} "
        "Use only information in the input payload. "
        "Return plain text without markdown code fences.\n\n"
        f"Input:\n{serialized_input}"
    )


def _extract_text_from_output(value: Any) -> str:
    """Extract representative text from prior phase output."""
    safe_value = _to_json_safe(value)

    if isinstance(safe_value, str):
        return safe_value

    if isinstance(safe_value, dict):
        for key in (
            "output",
            "content",
            "draft_content",
            "research_findings",
            "publish_ready_content",
            "assessment",
        ):
            candidate = safe_value.get(key)
            if isinstance(candidate, str) and candidate.strip():
                return candidate
            if isinstance(candidate, dict):
                nested = candidate.get("summary") or candidate.get("feedback")
                if isinstance(nested, str) and nested.strip():
                    return nested

        return json.dumps(safe_value, ensure_ascii=False)

    if isinstance(safe_value, list):
        return json.dumps(safe_value, ensure_ascii=False)

    return str(safe_value)


def _build_content_phase_fallback_result(
    phase_name: str,
    text: str,
    source: str,
    fallback_reason: str,
) -> Dict[str, Any]:
    """Build phase-appropriate fallback result payload."""
    normalized_phase = _normalize_phase_alias(phase_name)
    result: Dict[str, Any] = {
        "phase": phase_name,
        "output": text,
        "fallback_reason": fallback_reason,
        "fallback_source": source,
        "timestamp": datetime.now(timezone.utc).isoformat(),
    }

    if normalized_phase == "research":
        result["research_findings"] = text
    elif normalized_phase in {"draft", "refine"}:
        result["content"] = text
        result["draft_content"] = text
    elif normalized_phase == "assess":
        result["assessment"] = {
            "summary": text,
            "quality_score": 0.7,
            "feedback": text,
        }
        result["quality_score"] = 0.7
    elif normalized_phase in {"image", "image_selection"}:
        result["image_notes"] = text
        result["image_prompt"] = text
    elif normalized_phase in {"publish", "finalize"}:
        result["publish_ready_content"] = text
        result["title"] = "Workflow Generated Draft"
        result["summary"] = text[:240]

    return result


async def _execute_content_phase_fallback(
    phase_name: str,
    phase_input: Dict[str, Any],
    selected_model: Optional[str],
    fallback_reason: str,
) -> Dict[str, Any]:
    """Execute content phase using model-consolidation service with safe placeholder fallback."""
    prompt = _build_content_fallback_prompt(phase_name, phase_input)

    try:
        from services.model_consolidation_service import get_model_consolidation_service

        service = get_model_consolidation_service()
        response = await service.generate(
            prompt=prompt,
            model=selected_model,
            max_tokens=1200,
            temperature=0.4,
        )

        generated_text = getattr(response, "text", None) or str(response)
        return _build_content_phase_fallback_result(
            phase_name=phase_name,
            text=generated_text,
            source="model_consolidation_service",
            fallback_reason=fallback_reason,
        )
    except Exception as model_error:
        normalized_phase = _normalize_phase_alias(phase_name)
        phase_defaults = {
            "research": "Research notes generated from provided workflow inputs.",
            "draft": "Draft content generated from available workflow context.",
            "assess": "Assessment generated with baseline quality evaluation and recommendations.",
            "refine": "Refined content generated from prior workflow output and constraints.",
            "image": "Image guidance generated from content context and requested style.",
            "image_selection": "Image selection recommendations generated from workflow context.",
            "publish": "Publish-ready package generated from current workflow output.",
            "finalize": "Final publish-ready package generated from current workflow output.",
        }
        placeholder_text = phase_defaults.get(
            normalized_phase,
            f"Generated fallback output for phase '{phase_name}'.",
        )
        combined_reason = f"{fallback_reason}; model_fallback_error: {str(model_error)}"
        logger.error(
            f"[_execute_content_phase] Content phase fallback used placeholder output",
            exc_info=True,
            extra={
                "phase": phase_name,
                "selected_model": selected_model,
                "fallback_reason": combined_reason,
            },
        )
        return _build_content_phase_fallback_result(
            phase_name=phase_name,
            text=placeholder_text,
            source="deterministic_placeholder",
            fallback_reason=combined_reason,
        )


async def _execute_generic_phase_fallback(
    phase_name: str,
    phase_input: Dict[str, Any],
    selected_model: Optional[str],
    fallback_reason: str,
) -> Dict[str, Any]:
    """Execute a generic phase fallback for non-content phases."""
    prompt = _build_content_fallback_prompt(phase_name, phase_input)

    try:
        from services.model_consolidation_service import get_model_consolidation_service

        service = get_model_consolidation_service()
        response = await service.generate(
            prompt=prompt,
            model=selected_model,
            max_tokens=900,
            temperature=0.3,
        )
        generated_text = getattr(response, "text", None) or str(response)
        return {
            "phase": phase_name,
            "output": generated_text,
            "fallback_reason": fallback_reason,
            "fallback_source": "model_consolidation_service",
            "timestamp": datetime.now(timezone.utc).isoformat(),
        }
    except Exception as model_error:
        combined_reason = f"{fallback_reason}; model_fallback_error: {str(model_error)}"
        logger.warning(
            "Generic phase fallback used placeholder output",
            exc_info=True,
            extra={
                "phase": phase_name,
                "selected_model": selected_model,
                "fallback_reason": combined_reason,
            },
        )
        return {
            "phase": phase_name,
            "output": f"Fallback output generated for phase '{phase_name}'.",
            "fallback_reason": combined_reason,
            "fallback_source": "deterministic_placeholder",
            "timestamp": datetime.now(timezone.utc).isoformat(),
        }


async def create_phase_handler(
    phase_name: str,
    agent_name: str,
    database_service: Any,
    phase_metadata: Optional[Dict[str, Any]] = None,
) -> Callable:
    """
    Create an async handler for a workflow phase.

    The handler executes the specified agent/service for the phase.
    Supports multiple agent types with different execution methods.

    Args:
        phase_name: Name of the phase (e.g., 'research', 'draft')
        agent_name: Name of the agent to execute (e.g., 'content_agent', 'financial_agent')
        database_service: Database service for persistence

    Returns:
        Async callable handler function
    """

    resolved_agent_name = resolve_phase_agent_name(
        configured_agent=agent_name,
        phase_metadata=phase_metadata,
        phase_name=phase_name,
    )

    if resolved_agent_name != (agent_name or ""):
        logger.debug(
            "Resolved phase '%s' agent '%s' -> '%s'",
            phase_name,
            agent_name,
            resolved_agent_name,
        )

    async def phase_handler(context: Any, **kwargs) -> Any:
        """
        Execute a workflow phase by routing to appropriate agent.

        Supports agents with various execution methods:
        - async execute(input_data, **kwargs)
        - async run(input_data, **kwargs)
        - async process(input_data, **kwargs)
        - sync methods (wrapped in executor)

        Args:
            context: WorkflowContext with state and results
            **kwargs: Additional parameters

        Returns:
            Phase output payload (JSON-serializable dict)
        """
        import time

        start_time = time.time()

        try:
            logger.info(
                f"[{context.workflow_id}] Executing phase: {phase_name} "
                f"(agent: {resolved_agent_name})"
            )

            # Merge workflow-level input with phase-specific configured inputs
            base_input = context.initial_input or {}
            metadata = phase_metadata or {}
            phase_inputs = metadata.get("phase_inputs") or {}
            selected_model = metadata.get("selected_model")

            phase_input = {**base_input, **phase_inputs}
            if getattr(context, "accumulated_output", None) is not None:
                previous_output = _to_json_safe(context.accumulated_output)
                phase_input["previous_phase_output"] = previous_output
                phase_input["previous_phase_text"] = _extract_text_from_output(previous_output)
            if selected_model:
                phase_input["selected_model"] = selected_model

            execution_mode = "agent"
            agent_instance = None
            result: Any = None

            # Get and instantiate agent
            agent_instance = await _get_agent_instance_async(resolved_agent_name)

            if not agent_instance:
                fallback_reason = f"Could not instantiate agent: {resolved_agent_name}"
                if _is_content_phase_for_fallback(phase_name, metadata):
                    logger.warning(
                        f"[{context.workflow_id}] {fallback_reason}; using fallback execution"
                    )
                    execution_mode = "fallback"
                    result = await _execute_content_phase_fallback(
                        phase_name=phase_name,
                        phase_input=phase_input,
                        selected_model=selected_model,
                        fallback_reason=fallback_reason,
                    )
                else:
                    logger.warning(
                        f"[{context.workflow_id}] {fallback_reason}; using generic fallback execution"
                    )
                    execution_mode = "fallback_generic"
                    result = await _execute_generic_phase_fallback(
                        phase_name=phase_name,
                        phase_input=phase_input,
                        selected_model=selected_model,
                        fallback_reason=fallback_reason,
                    )
            else:
                logger.debug(
                    f"[{context.workflow_id}] Instantiated agent {resolved_agent_name}: "
                    f"{type(agent_instance).__name__}"
                )

                # Call agent with appropriate method
                try:
                    result = await _execute_agent_method(
                        agent_instance,
                        resolved_agent_name,
                        phase_name,
                        phase_input,
                        context,
                    )
                except Exception as agent_exec_error:
                    fallback_reason = (
                        f"Agent execution failed for {resolved_agent_name}: "
                        f"{str(agent_exec_error)}"
                    )
                    if _is_content_phase_for_fallback(phase_name, metadata):
                        logger.warning(
                            f"[{context.workflow_id}] {fallback_reason}; using fallback execution"
                        )
                        execution_mode = "fallback"
                        result = await _execute_content_phase_fallback(
                            phase_name=phase_name,
                            phase_input=phase_input,
                            selected_model=selected_model,
                            fallback_reason=fallback_reason,
                        )
                    else:
                        logger.warning(
                            f"[{context.workflow_id}] {fallback_reason}; using generic fallback execution"
                        )
                        execution_mode = "fallback_generic"
                        result = await _execute_generic_phase_fallback(
                            phase_name=phase_name,
                            phase_input=phase_input,
                            selected_model=selected_model,
                            fallback_reason=fallback_reason,
                        )

            duration_ms = int((time.time() - start_time) * 1000)

            logger.info(
                f"[{context.workflow_id}] Phase completed: {phase_name} " f"({duration_ms}ms)"
            )

            phase_output = result if isinstance(result, dict) else {"output": result}
            phase_output["_phase_metadata"] = {
                "agent": resolved_agent_name,
                "agent_type": type(agent_instance).__name__ if agent_instance else None,
                "selected_model": selected_model,
                "phase_inputs": phase_inputs,
                "duration_ms": duration_ms,
                "execution_mode": execution_mode,
            }
            safe_output = _to_json_safe(phase_output)
            if isinstance(safe_output, dict):
                return safe_output
            return {
                "phase": phase_name,
                "output": str(safe_output),
                "_phase_metadata": phase_output["_phase_metadata"],
            }

        except Exception as e:
            duration_ms = int((time.time() - start_time) * 1000)
            logger.error(
                f"[{context.workflow_id}] Phase failed: {phase_name} "
                f"(agent: {resolved_agent_name}) - {str(e)}",
                exc_info=True,
            )
            raise

    return phase_handler


async def _get_agent_instance_async(agent_name: str) -> Any:
    """
    Get an agent instance by name.

    Uses the UnifiedOrchestrator pattern with registry and fallback imports.

    Args:
        agent_name: Name of agent to instantiate

    Returns:
        Instantiated agent instance or None
    """
    try:
        from services.unified_orchestrator import UnifiedOrchestrator

        # Create orchestrator and use its agent instantiation logic
        orchestrator = UnifiedOrchestrator()
        return orchestrator._get_agent_instance(agent_name)

    except Exception as e:
        logger.error(
            f"[_get_agent_instance_async] Could not instantiate agent {agent_name}: {e}",
            exc_info=True,
        )
        return None


async def _execute_agent_method(
    agent: Any, agent_name: str, phase_name: str, input_data: Dict[str, Any], context: Any
) -> Dict[str, Any]:
    """
    Execute an appropriate method on the agent.

    Tries multiple execution patterns:
    1. execute(input_data, phase_name=...) - most explicit
    2. run(input_data) - common pattern
    3. process(input_data) - alternative pattern
    4. Sync method in executor - fallback for sync agents

    Args:
        agent: Instantiated agent object
        agent_name: Name of agent (for logging)
        phase_name: Name of phase (for context)
        input_data: Input data for agent
        context: Workflow context

    Returns:
        Structured result from agent execution
    """
    import inspect

    logger.debug(f"Agent {agent_name} methods: {[m for m in dir(agent) if not m.startswith('_')]}")

    # Try execute() method first
    if hasattr(agent, "execute") and callable(getattr(agent, "execute")):
        execute_method = getattr(agent, "execute")
        if inspect.iscoroutinefunction(execute_method):
            logger.debug(f"Calling async execute() on {agent_name}")
            result = await execute_method(input_data, phase_name=phase_name)
        else:
            logger.debug(f"Calling sync execute() on {agent_name} in executor")
            loop = asyncio.get_event_loop()
            result = await loop.run_in_executor(
                None, lambda: execute_method(input_data, phase_name=phase_name)
            )

    # Try run() method
    elif hasattr(agent, "run") and callable(getattr(agent, "run")):
        run_method = getattr(agent, "run")
        if inspect.iscoroutinefunction(run_method):
            logger.debug(f"Calling async run() on {agent_name}")
            result = await run_method(input_data)
        else:
            logger.debug(f"Calling sync run() on {agent_name} in executor")
            loop = asyncio.get_event_loop()
            result = await loop.run_in_executor(None, lambda: run_method(input_data))

    # Try process() method
    elif hasattr(agent, "process") and callable(getattr(agent, "process")):
        process_method = getattr(agent, "process")
        if inspect.iscoroutinefunction(process_method):
            logger.debug(f"Calling async process() on {agent_name}")
            result = await process_method(input_data)
        else:
            logger.debug(f"Calling sync process() on {agent_name} in executor")
            loop = asyncio.get_event_loop()
            result = await loop.run_in_executor(None, lambda: process_method(input_data))

    else:
        raise ValueError(
            f"Agent {agent_name} has no callable execute/run/process methods. "
            f"Available methods: {[m for m in dir(agent) if not m.startswith('_')]}"
        )

    # Wrap result if not already structured
    if isinstance(result, dict):
        return result
    elif isinstance(result, str):
        return {
            "phase": phase_name,
            "output": result,
            "timestamp": datetime.now(timezone.utc).isoformat(),
        }
    else:
        return {
            "phase": phase_name,
            "output": str(result),
            "timestamp": datetime.now(timezone.utc).isoformat(),
        }


async def execute_custom_workflow(
    custom_workflow: Any,
    input_data: Dict[str, Any],
    database_service: Any,
    queue_async: bool = True,
) -> Dict[str, Any]:
    """
    Execute a custom workflow.

    Args:
        custom_workflow: CustomWorkflow object from database
        input_data: Input data for workflow execution
        database_service: DatabaseService instance
        queue_async: If True, execute asynchronously and return execution ID
                    If False, execute synchronously and return results

    Returns:
        Execution response with execution_id, status, and progress
    """

    from services.workflow_engine import WorkflowContext, WorkflowEngine, WorkflowPhase

    try:
        execution_id = str(uuid.uuid4())

        # Create workflow context
        context = WorkflowContext(
            workflow_id=str(custom_workflow.id),
            request_id=execution_id,
            initial_input=input_data,
            tags=custom_workflow.tags or [],
        )

        # Convert CustomWorkflow phases to WorkflowPhase objects
        phases: List[WorkflowPhase] = []

        for phase_config in custom_workflow.phases:
            if hasattr(phase_config, "model_dump"):
                phase_data = phase_config.model_dump()
            elif isinstance(phase_config, dict):
                phase_data = phase_config
            else:
                phase_data = {
                    "name": getattr(phase_config, "name", None),
                    "agent": getattr(phase_config, "agent", None),
                    "description": getattr(phase_config, "description", ""),
                    "timeout_seconds": getattr(phase_config, "timeout_seconds", 300),
                    "max_retries": getattr(phase_config, "max_retries", 2),
                    "skip_on_error": getattr(phase_config, "skip_on_error", False),
                    "required": getattr(phase_config, "required", True),
                    "quality_threshold": getattr(phase_config, "quality_threshold", None),
                    "metadata": getattr(phase_config, "metadata", {}),
                }

            # Get handler for this phase
            handler = await create_phase_handler(
                phase_name=phase_data.get("name"),  # type: ignore[arg-type]
                agent_name=phase_data.get("agent"),  # type: ignore[arg-type]
                database_service=database_service,
                phase_metadata=phase_data.get("metadata") or {},
            )

            # Create WorkflowPhase with configuration from custom workflow
            phase = WorkflowPhase(
                name=phase_data.get("name"),  # type: ignore[arg-type]
                handler=handler,
                description=phase_data.get("description", ""),
                timeout_seconds=phase_data.get("timeout_seconds", 300),
                max_retries=phase_data.get("max_retries", 2),
                skip_on_error=phase_data.get("skip_on_error", False),
                required=phase_data.get("required", True),
                metadata=phase_data.get("metadata", {}),
            )
            phases.append(phase)

        logger.info(
            f"[{execution_id}] Created workflow with {len(phases)} phases: "
            f"{', '.join(p.name for p in phases)}"
        )

        # Execute workflow
        if queue_async:
            # Queue for async execution
            logger.info(f"[{execution_id}] Queueing async execution")

            # INTERIM SOLUTION: Using asyncio.create_task for background execution
            # This works for single-instance deployments but lacks:
            # - Persistence across restarts
            # - Distributed task queue support
            # - Advanced monitoring and retries
            #
            # TODO: Phase 2 - Implement robust async task queue (Celery, RQ, or Arq)
            # Migration path:
            # 1. Install Celery: pip install celery redis
            # 2. Create celery_app.py with Redis broker configuration
            # 3. Create celery task wrapper for execute_custom_workflow_execution
            # 4. Replace asyncio.create_task with celery task.delay()
            # 5. Add monitoring via Celery Flower (celery -A celery_app flower)
            #
            # Example Phase 2 code (pseudocode):
            # from celery import Celery
            # celery_app = Celery('workflows', broker='redis://localhost:6379')
            # @celery_app.task def execute_workflow_task(...):
            #     return await execute_custom_workflow_execution(...)

            asyncio.create_task(
                _execute_workflow_background(phases, context, custom_workflow, database_service)
            )

            return {
                "execution_id": execution_id,
                "workflow_id": str(custom_workflow.id),
                "status": "pending",
                "started_at": context.started_at.isoformat(),
                "phases": [p.name for p in phases],
                "progress_percent": 0,
            }
        else:
            # Execute synchronously
            engine = WorkflowEngine(database_service=database_service)
            final_context = await engine.execute_workflow(phases, context)

            return {
                "execution_id": execution_id,
                "workflow_id": str(custom_workflow.id),
                "status": final_context.status.value,
                "started_at": final_context.started_at.isoformat(),
                "phases": [p.name for p in phases],
                "results": (
                    {name: result.to_dict() for name, result in final_context.results.items()}
                    if final_context.results
                    else {}
                ),
                "progress_percent": 100 if final_context.status.value == "completed" else 0,
            }

    except Exception as e:
        logger.error(
            f"[_execute_workflow_task] Failed to execute workflow: {str(e)}", exc_info=True
        )
        raise


async def _execute_workflow_background(
    phases: List[Any],
    context: Any,
    custom_workflow: Any,
    database_service: Any,
) -> None:
    """
    Execute workflow in the background.

    Args:
        phases: List of WorkflowPhase objects
        context: WorkflowContext
        custom_workflow: CustomWorkflow object
        database_service: DatabaseService instance
    """

    try:
        from services.workflow_engine import WorkflowEngine

        logger.info(f"[{context.workflow_id}] Starting background execution")

        engine = WorkflowEngine(database_service=database_service)
        final_context = await engine.execute_workflow(phases, context)

        logger.info(
            f"[{context.workflow_id}] Background execution completed: "
            f"{final_context.status.value}"
        )

        # Calculate duration and prepare results
        from datetime import datetime, timezone

        # Calculate duration from phase results
        duration_ms = 0
        if final_context.results:
            duration_ms = int(
                sum(
                    r.duration_ms
                    for r in final_context.results.values()
                    if r.duration_ms is not None
                )
            )

        # Convert phase results to JSON-serializable dict
        phase_results = {}
        if final_context.results:
            for phase_name, phase_result in final_context.results.items():
                phase_results[phase_name] = {
                    "status": (
                        phase_result.status.value
                        if hasattr(phase_result.status, "value")
                        else str(phase_result.status)
                    ),
                    "output": _to_json_safe(phase_result.output),
                    "error": phase_result.error,
                    "duration_ms": phase_result.duration_ms,
                    "metadata": _to_json_safe(phase_result.metadata or {}),
                }

        # Count completed phases
        completed_phases_count = len(
            [r for r in phase_results.values() if r.get("status") == "completed"]
        )
        total_phases_count = len(phases) if phases else 0
        progress = (
            int((completed_phases_count / total_phases_count * 100))
            if total_phases_count > 0
            else 0
        )

        # Persist execution results
        from services.custom_workflows_service import CustomWorkflowsService

        workflows_service = CustomWorkflowsService(database_service)

        error_message = None
        if (
            getattr(final_context, "status", None)
            and str(final_context.status.value).lower() == "failed"
        ):
            for phase_name in list(final_context.results.keys())[::-1]:
                phase_result = final_context.results[phase_name]
                if getattr(phase_result, "error", None):
                    error_message = f"{phase_name}: {phase_result.error}"
                    break

        persist_success = await workflows_service.persist_workflow_execution(
            execution_id=context.request_id,
            workflow_id=str(custom_workflow.id),
            owner_id=custom_workflow.owner_id,
            execution_status=final_context.status.value,
            phase_results=phase_results,
            duration_ms=duration_ms,
            initial_input=_to_json_safe(context.initial_input),
            final_output=_to_json_safe(context.accumulated_output),
            error_message=error_message,
            completed_phases=completed_phases_count,
            total_phases=total_phases_count,
            progress_percent=progress,
            tags=_to_json_safe(custom_workflow.tags or []),
            metadata=_to_json_safe(
                {
                    "execution_id": context.request_id,
                    "workflow_name": custom_workflow.name,
                    "phase_count": total_phases_count,
                }
            ),
        )

        if persist_success:
            logger.info(f"[{context.workflow_id}] Execution results persisted to database")
        else:
            logger.warning(f"[{context.workflow_id}] Failed to persist execution results")

    except Exception as e:
        logger.error(
            f"[{context.workflow_id}] Background execution failed: {str(e)}", exc_info=True
        )


def convert_phases_to_schemas(phases: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """
    Convert raw phase configurations to schema-validated objects.

    Args:
        phases: List of phase configuration dictionaries

    Returns:
        List of validated phase configurations
    """

    from schemas.custom_workflow_schemas import PhaseConfig

    validated_phases = []

    for phase_config in phases:
        # Validate and convert to PhaseConfig
        phase_name = phase_config.get("name") or "unknown"
        phase_agent = phase_config.get("agent") or "unknown"

        validated = PhaseConfig(
            name=phase_name,
            agent=phase_agent,
            description=phase_config.get("description", ""),
            timeout_seconds=phase_config.get("timeout_seconds", 300),
            max_retries=phase_config.get("max_retries", 2),
            skip_on_error=phase_config.get("skip_on_error", False),
            required=phase_config.get("required", True),
            quality_threshold=phase_config.get("quality_threshold"),
            metadata=phase_config.get("metadata", {}),
        )
        validated_phases.append(validated)

    return validated_phases
