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
import uuid
from datetime import datetime, timezone
from typing import Any, Callable, Dict, List, Optional

logger = logging.getLogger(__name__)


async def create_phase_handler(
    phase_name: str, agent_name: str, database_service: Any
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
            PhaseResult with execution status and output
        """
        from services.workflow_engine import PhaseResult, PhaseStatus
        import inspect
        import time
        
        start_time = time.time()
        
        try:
            logger.info(
                f"[{context.workflow_id}] Executing phase: {phase_name} "
                f"(agent: {agent_name})"
            )
            
            # Get phase input from context
            phase_input = context.initial_input or {}
            
            # Get and instantiate agent
            agent_instance = await _get_agent_instance_async(agent_name)
            
            if not agent_instance:
                raise ValueError(f"Could not instantiate agent: {agent_name}")
            
            logger.debug(
                f"[{context.workflow_id}] Instantiated agent {agent_name}: "
                f"{type(agent_instance).__name__}"
            )
            
            # Call agent with appropriate method
            result = await _execute_agent_method(
                agent_instance, agent_name, phase_name, phase_input, context
            )
            
            duration_ms = int((time.time() - start_time) * 1000)
            
            logger.info(
                f"[{context.workflow_id}] Phase completed: {phase_name} "
                f"({duration_ms}ms)"
            )
            
            return PhaseResult(
                phase_name=phase_name,
                status=PhaseStatus.COMPLETED,
                output=result,
                duration_ms=duration_ms,
                retry_count=0,
                metadata={
                    "agent": agent_name,
                    "agent_type": type(agent_instance).__name__
                }
            )
            
        except Exception as e:
            duration_ms = int((time.time() - start_time) * 1000)
            logger.error(
                f"[{context.workflow_id}] Phase failed: {phase_name} - {str(e)}",
                exc_info=True
            )
            
            from services.workflow_engine import PhaseResult, PhaseStatus
            
            return PhaseResult(
                phase_name=phase_name,
                status=PhaseStatus.FAILED,
                error=str(e),
                duration_ms=duration_ms,
                retry_count=0,
                metadata={"agent": agent_name, "error_type": type(e).__name__}
            )
    
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
        logger.warning(f"Could not instantiate agent {agent_name}: {e}")
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
    if hasattr(agent, 'execute') and callable(getattr(agent, 'execute')):
        execute_method = getattr(agent, 'execute')
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
    elif hasattr(agent, 'run') and callable(getattr(agent, 'run')):
        run_method = getattr(agent, 'run')
        if inspect.iscoroutinefunction(run_method):
            logger.debug(f"Calling async run() on {agent_name}")
            result = await run_method(input_data)
        else:
            logger.debug(f"Calling sync run() on {agent_name} in executor")
            loop = asyncio.get_event_loop()
            result = await loop.run_in_executor(None, lambda: run_method(input_data))
    
    # Try process() method
    elif hasattr(agent, 'process') and callable(getattr(agent, 'process')):
        process_method = getattr(agent, 'process')
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
            "timestamp": datetime.now(timezone.utc).isoformat()
        }
    else:
        return {
            "phase": phase_name,
            "output": str(result),
            "timestamp": datetime.now(timezone.utc).isoformat()
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
    
    from services.workflow_engine import WorkflowEngine, WorkflowContext, WorkflowPhase
    
    try:
        execution_id = str(uuid.uuid4())
        
        # Create workflow context
        context = WorkflowContext(
            workflow_id=str(custom_workflow.id),
            request_id=execution_id,
            initial_input=input_data,
            tags=custom_workflow.tags or []
        )
        
        # Convert CustomWorkflow phases to WorkflowPhase objects
        phases: List[WorkflowPhase] = []
        
        for phase_config in custom_workflow.phases:
            # Get handler for this phase
            handler = await create_phase_handler(
                phase_name=phase_config.get("name"),
                agent_name=phase_config.get("agent"),
                database_service=database_service
            )
            
            # Create WorkflowPhase with configuration from custom workflow
            phase = WorkflowPhase(
                name=phase_config.get("name"),
                handler=handler,
                description=phase_config.get("description", ""),
                timeout_seconds=phase_config.get("timeout_seconds", 300),
                max_retries=phase_config.get("max_retries", 2),
                skip_on_error=phase_config.get("skip_on_error", False),
                required=phase_config.get("required", True),
                metadata=phase_config.get("metadata", {})
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
            
            # TODO: Implement async task queue (Celery, RQ, or custom)
            # For now, start background task
            asyncio.create_task(
                _execute_workflow_background(
                    phases, context, custom_workflow, database_service
                )
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
                "results": {
                    name: result.to_dict()
                    for name, result in final_context.results.items()
                }
                if final_context.results
                else {},
                "progress_percent": 100 if final_context.status.value == "completed" else 0,
            }
        
    except Exception as e:
        logger.error(f"Failed to execute workflow: {str(e)}", exc_info=True)
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
            duration_ms = int(sum(
                r.duration_ms for r in final_context.results.values() 
                if r.duration_ms is not None
            ))
        
        # Convert phase results to JSON-serializable dict
        phase_results = {}
        if final_context.results:
            for phase_name, phase_result in final_context.results.items():
                phase_results[phase_name] = {
                    "status": phase_result.status.value if hasattr(phase_result.status, 'value') else str(phase_result.status),
                    "output": phase_result.output,
                    "error": phase_result.error,
                    "duration_ms": phase_result.duration_ms,
                    "metadata": phase_result.metadata or {},
                }
        
        # Count completed phases
        completed_phases_count = len([r for r in phase_results.values() if r.get("status") == "completed"])
        total_phases_count = len(phases) if phases else 0
        progress = int((completed_phases_count / total_phases_count * 100)) if total_phases_count > 0 else 0
        
        # Persist execution results
        from services.custom_workflows_service import CustomWorkflowsService
        
        workflows_service = CustomWorkflowsService(database_service)
        
        persist_success = await workflows_service.persist_workflow_execution(
            execution_id=context.request_id,
            workflow_id=str(custom_workflow.id),
            owner_id=custom_workflow.owner_id,
            execution_status=final_context.status.value,
            phase_results=phase_results,
            duration_ms=duration_ms,
            initial_input=context.initial_input,
            final_output=context.accumulated_output,
            error_message=None,
            completed_phases=completed_phases_count,
            total_phases=total_phases_count,
            progress_percent=progress,
            tags=custom_workflow.tags or [],
            metadata={
                "execution_id": context.request_id,
                "workflow_name": custom_workflow.name,
                "phase_count": total_phases_count,
            }
        )
        
        if persist_success:
            logger.info(f"[{context.workflow_id}] Execution results persisted to database")
        else:
            logger.warning(f"[{context.workflow_id}] Failed to persist execution results")
        
    except Exception as e:
        logger.error(
            f"[{context.workflow_id}] Background execution failed: {str(e)}",
            exc_info=True
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
