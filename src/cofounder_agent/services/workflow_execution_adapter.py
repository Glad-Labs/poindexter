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
    
    Args:
        phase_name: Name of the phase (e.g., 'research', 'draft')
        agent_name: Name of the agent to execute (e.g., 'content_agent')
        database_service: Database service for persistence
        
    Returns:
        Async callable handler function
    """
    
    async def phase_handler(context: Any, **kwargs) -> Any:
        """
        Execute a workflow phase.
        
        Args:
            context: WorkflowContext with state and results
            **kwargs: Additional parameters
            
        Returns:
            Result of phase execution
        """
        from services.workflow_engine import PhaseResult, PhaseStatus
        
        try:
            logger.info(f"[{context.workflow_id}] Executing phase: {phase_name}")
            
            # TODO: Implement actual agent execution based on agent_name
            # For now, return mock result
            # In production, this would:
            # 1. Load the appropriate agent/service
            # 2. Parse context.initial_input for phase input
            # 3. Call agent with phase-specific parameters
            # 4. Return structured result
            
            # Mock execution
            await asyncio.sleep(0.1)  # Simulate work
            
            result = {
                "phase": phase_name,
                "status": "completed",
                "output": f"Completed {phase_name} phase with {agent_name}",
                "timestamp": datetime.now(timezone.utc).isoformat(),
            }
            
            return PhaseResult(
                phase_name=phase_name,
                status=PhaseStatus.COMPLETED,
                output=result,
                duration_ms=100,
                retry_count=0,
                metadata={"agent": agent_name}
            )
            
        except Exception as e:
            logger.error(f"[{context.workflow_id}] Phase failed: {phase_name} - {str(e)}")
            
            from services.workflow_engine import PhaseResult, PhaseStatus
            
            return PhaseResult(
                phase_name=phase_name,
                status=PhaseStatus.FAILED,
                error=str(e),
                duration_ms=0,
                retry_count=0,
                metadata={"agent": agent_name}
            )
    
    return phase_handler


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
        
        # TODO: Store execution results in workflow_executions table
        # await database_service.persist_workflow_execution(
        #     execution_id=context.request_id,
        #     workflow_id=str(custom_workflow.id),
        #     status=final_context.status.value,
        #     results=final_context.results,
        #     duration_ms=0,  # Calculate from timestamps
        # )
        
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
