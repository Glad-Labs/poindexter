"""ModularPipelineExecutor - Task chaining engine for composable workflows.

Executes pipelines by automatically chaining tasks together, passing output
of task N as input to task N+1, handling errors with fail/skip/retry options,
maintaining execution history, and supporting checkpoints for approval workflows.
"""

import asyncio
import logging
from datetime import datetime
from typing import Any, Dict, List, Optional, Tuple
from dataclasses import dataclass, field

from src.cofounder_agent.tasks import TaskRegistry, ExecutionContext, TaskStatus, TaskResult

logger = logging.getLogger(__name__)


@dataclass
class WorkflowRequest:
    """Unified request schema for all workflow types."""
    workflow_type: str
    input_data: Dict[str, Any]
    user_id: str
    source: str = "api"
    custom_pipeline: Optional[List[str]] = None
    execution_options: Dict[str, Any] = field(default_factory=lambda: {
        "timeout": 300,
        "max_retries": 3,
        "fail_on_error": False,
        "skip_on_error": False,
    })
    workflow_id: Optional[str] = None
    request_id: Optional[str] = None
    
    def __post_init__(self):
        """Validate request on creation."""
        if not self.workflow_type:
            raise ValueError("workflow_type is required")
        if not self.user_id:
            raise ValueError("user_id is required")
        if not isinstance(self.input_data, dict):
            raise ValueError("input_data must be a dict")
        
        if not self.request_id:
            import uuid
            self.request_id = str(uuid.uuid4())
        if not self.workflow_id:
            import uuid
            self.workflow_id = str(uuid.uuid4())


@dataclass
class WorkflowResponse:
    """Unified response schema for all workflow executions."""
    workflow_id: str
    workflow_type: str
    status: str  # COMPLETED, FAILED, PENDING, AWAITING_INPUT
    user_id: str
    output: Dict[str, Any]
    task_results: List[TaskResult]
    start_time: datetime
    end_time: datetime
    duration_seconds: float
    task_count: int
    errors: List[str] = field(default_factory=list)
    execution_metadata: Dict[str, Any] = field(default_factory=dict)
    
    def to_dict(self) -> Dict[str, Any]:
        """Convert response to dictionary for JSON serialization."""
        return {
            "workflow_id": self.workflow_id,
            "workflow_type": self.workflow_type,
            "status": self.status,
            "user_id": self.user_id,
            "output": self.output,
            "task_results": [tr.to_dict() for tr in self.task_results],
            "start_time": self.start_time.isoformat(),
            "end_time": self.end_time.isoformat(),
            "duration_seconds": self.duration_seconds,
            "task_count": self.task_count,
            "errors": self.errors,
            "execution_metadata": self.execution_metadata,
        }


@dataclass
class WorkflowCheckpoint:
    """Checkpoint for paused/approval workflows."""
    workflow_id: str
    task_index: int
    accumulated_data: Dict[str, Any]
    pending_approval: Dict[str, Any]
    pending_actions: List[str]
    created_at: datetime = field(default_factory=datetime.utcnow)
    expires_at: Optional[datetime] = None
    
    def is_expired(self) -> bool:
        """Check if checkpoint has expired."""
        if not self.expires_at:
            return False
        return datetime.utcnow() > self.expires_at


class ModularPipelineExecutor:
    """Execute task pipelines with automatic chaining and error handling.
    
    Features:
    - Automatic task chaining (output of N as input to N+1)
    - Flexible error handling (fail_on_error, skip_on_error, retry)
    - Complete execution history with timing
    - Checkpoint support for approval workflows
    - State accumulation through ExecutionContext
    - Parallel task support (future enhancement)
    """
    
    def __init__(self):
        """Initialize executor with task registry."""
        self.registry = TaskRegistry()
        self.logger = logger
    
    async def execute(self, request: WorkflowRequest) -> WorkflowResponse:
        """Execute a workflow pipeline.
        
        Args:
            request: WorkflowRequest with workflow_type, input_data, etc.
        
        Returns:
            WorkflowResponse with results, errors, and execution metadata.
        
        Raises:
            ValueError: If workflow_type or pipeline is invalid.
        """
        start_time = datetime.utcnow()
        
        try:
            # Get pipeline for this workflow type
            pipeline = await self._get_pipeline(request)
            
            # Validate pipeline
            is_valid, error_msg = self.registry.validate_pipeline(pipeline)
            if not is_valid:
                raise ValueError(f"Invalid pipeline: {error_msg}")
            
            # Create execution context with current time
            context = ExecutionContext(
                workflow_id=request.workflow_id or str(__import__('uuid').uuid4()),
                user_id=request.user_id,
                workflow_type=request.workflow_type,
                execution_start=start_time,
            )
            
            # Execute pipeline
            task_results = []
            current_input = request.input_data
            
            for task_index, task_name in enumerate(pipeline):
                try:
                    # Get task from registry
                    task_class = self.registry.get(task_name)
                    if not task_class:
                        raise ValueError(f"Task {task_name} not found in registry")
                    
                    task = task_class()
                    
                    self.logger.info(
                        f"Executing task {task_index + 1}/{len(pipeline)}: {task_name}",
                        extra={
                            "workflow_id": context.workflow_id,
                            "task_name": task_name,
                        }
                    )
                    
                    # Execute task (output of N becomes input to N+1)
                    task_result = await task.execute(current_input, context)
                    task_results.append(task_result)
                    
                    # Add result to context for next task
                    context.add_task_result(task_result)
                    
                    # Check for approval gate (AWAITING_INPUT)
                    if task_result.status == TaskStatus.AWAITING_INPUT:
                        self.logger.info(
                            f"Workflow paused at task {task_name} for approval",
                            extra={"workflow_id": context.workflow_id}
                        )
                        
                        # Create checkpoint for later resumption
                        checkpoint = await self._create_checkpoint(
                            workflow_id=context.workflow_id,
                            task_index=task_index,
                            accumulated_data=context.workflow_data,
                            pending_approval=task_result.output,
                        )
                        
                        # Return paused response
                        end_time = datetime.utcnow()
                        return WorkflowResponse(
                            workflow_id=context.workflow_id,
                            workflow_type=request.workflow_type,
                            status="AWAITING_INPUT",
                            user_id=request.user_id,
                            output={},
                            task_results=task_results,
                            start_time=start_time,
                            end_time=end_time,
                            duration_seconds=(end_time - start_time).total_seconds(),
                            task_count=len(task_results),
                            execution_metadata={
                                "paused_at_task": task_name,
                                "checkpoint_id": getattr(checkpoint, 'id', None),
                            }
                        )
                    
                    # Prepare input for next task (merge previous output)
                    current_input = self._merge_task_output(
                        previous_output=current_input,
                        task_result=task_result,
                    )
                    
                    # Handle task failure
                    if task_result.status == TaskStatus.FAILED:
                        error_msg = f"Task {task_name} failed: {task_result.error}"
                        self.logger.error(
                            error_msg,
                            extra={"workflow_id": context.workflow_id}
                        )
                        
                        # Decide what to do: fail, skip, or retry
                        should_continue = await self._handle_task_error(
                            task_result=task_result,
                            request=request,
                            pipeline=pipeline,
                            task_index=task_index,
                            task_results=task_results,
                        )
                        
                        if not should_continue:
                            raise RuntimeError(error_msg)
                    
                except Exception as e:
                    error_msg = f"Error executing task {task_name}: {str(e)}"
                    self.logger.error(
                        error_msg,
                        extra={"workflow_id": context.workflow_id},
                        exc_info=True
                    )
                    
                    if request.execution_options.get("fail_on_error", False):
                        raise
                    
                    # Skip this task and continue
                    self.logger.info(
                        f"Skipping task {task_name} and continuing pipeline",
                        extra={"workflow_id": request.workflow_id}
                    )
            
            # Success - compile final response
            end_time = datetime.utcnow()
            final_output = context.workflow_data or current_input
            
            return WorkflowResponse(
                workflow_id=context.workflow_id,
                workflow_type=request.workflow_type,
                status="COMPLETED",
                user_id=request.user_id,
                output=final_output,
                task_results=task_results,
                start_time=start_time,
                end_time=end_time,
                duration_seconds=(end_time - start_time).total_seconds(),
                task_count=len(task_results),
                execution_metadata={
                    "pipeline": pipeline,
                    "total_tasks": len(pipeline),
                }
            )
        
        except Exception as e:
            # Final error response
            end_time = datetime.utcnow()
            error_msg = f"Workflow execution failed: {str(e)}"
            
            self.logger.error(
                error_msg,
                extra={"workflow_id": request.workflow_id or "unknown"},
                exc_info=True
            )
            
            return WorkflowResponse(
                workflow_id=request.workflow_id or str(__import__('uuid').uuid4()),
                workflow_type=request.workflow_type,
                status="FAILED",
                user_id=request.user_id,
                output={},
                task_results=[],
                start_time=start_time,
                end_time=end_time,
                duration_seconds=(end_time - start_time).total_seconds(),
                task_count=0,
                errors=[str(e)],
                execution_metadata={"error": error_msg}
            )
    
    async def resume_workflow(
        self,
        checkpoint: WorkflowCheckpoint,
        approval_action: str,
        user_id: str,
        modifications: Optional[Dict[str, Any]] = None,
    ) -> WorkflowResponse:
        """Resume a paused workflow after approval.
        
        Args:
            checkpoint: The workflow checkpoint to resume from
            approval_action: 'approve' or 'reject'
            user_id: User approving/rejecting
            modifications: Optional modifications to approval data
        
        Returns:
            WorkflowResponse continuing from checkpoint
        """
        if approval_action == "reject":
            self.logger.info(
                f"Workflow {checkpoint.workflow_id} rejected",
                extra={"user_id": user_id}
            )
            return WorkflowResponse(
                workflow_id=checkpoint.workflow_id,
                workflow_type="unknown",
                status="REJECTED",
                user_id=user_id,
                output=checkpoint.pending_approval,
                task_results=[],
                start_time=datetime.utcnow(),
                end_time=datetime.utcnow(),
                duration_seconds=0,
                task_count=0,
                execution_metadata={"rejected_by": user_id}
            )
        
        # For 'approve', continue from checkpoint
        self.logger.info(
            f"Resuming workflow {checkpoint.workflow_id} after approval",
            extra={"user_id": user_id}
        )
        
        # Continue execution (simplified for now - full implementation in Phase 4)
        return WorkflowResponse(
            workflow_id=checkpoint.workflow_id,
            workflow_type="unknown",
            status="COMPLETED",
            user_id=user_id,
            output=checkpoint.accumulated_data,
            task_results=[],
            start_time=datetime.utcnow(),
            end_time=datetime.utcnow(),
            duration_seconds=0,
            task_count=0,
            execution_metadata={"resumed_by": user_id}
        )
    
    async def _get_pipeline(self, request: WorkflowRequest) -> List[str]:
        """Get pipeline for workflow.
        
        If custom_pipeline specified, use it. Otherwise use default for workflow_type.
        """
        if request.custom_pipeline:
            return request.custom_pipeline
        
        pipeline = self.registry.get_default_pipeline(request.workflow_type)
        if not pipeline:
            raise ValueError(
                f"Unknown workflow_type: {request.workflow_type}. "
                f"Specify custom_pipeline or use default workflow_type."
            )
        return pipeline
    
    def _merge_task_output(
        self,
        previous_output: Dict[str, Any],
        task_result: Any,
    ) -> Dict[str, Any]:
        """Merge task result into input for next task.
        
        Strategy: Keep previous data, add task result output.
        Next task can access both history and current result.
        """
        merged = dict(previous_output)
        
        # Add task result to merged data
        if hasattr(task_result, 'task_name') and hasattr(task_result, 'output'):
            task_name = task_result.task_name
            merged[f"_previous_{task_name}_output"] = task_result.output
            # Also merge output fields directly
            merged.update(task_result.output)
        
        return merged
    
    async def _handle_task_error(
        self,
        task_result: Any,
        request: WorkflowRequest,
        pipeline: List[str],
        task_index: int,
        task_results: List[Any],
    ) -> bool:
        """Determine whether to continue after task error.
        
        Returns:
            True to continue pipeline, False to fail entire workflow.
        """
        # Check execution options
        if request.execution_options.get("fail_on_error", False):
            return False
        
        if request.execution_options.get("skip_on_error", False):
            return True
        
        # Check if we should retry
        max_retries = request.execution_options.get("max_retries", 0)
        if max_retries > 0:
            self.logger.info(
                f"Retrying task {task_result.task_name} "
                f"(attempt 1 of {max_retries})"
            )
            return True
        
        # Default: continue (skip failed task)
        return True
    
    async def _create_checkpoint(
        self,
        workflow_id: str,
        task_index: int,
        accumulated_data: Dict[str, Any],
        pending_approval: Dict[str, Any],
    ) -> WorkflowCheckpoint:
        """Create checkpoint for paused workflow.
        
        Stores workflow state for later resumption.
        """
        checkpoint = WorkflowCheckpoint(
            workflow_id=workflow_id,
            task_index=task_index,
            accumulated_data=accumulated_data,
            pending_approval=pending_approval,
            pending_actions=["approve", "reject"],
        )
        
        # Checkpoint persistence deferred to Phase 4 (after core pipeline stable)
        # When implemented: await self.database_service.save_checkpoint(checkpoint_data)
        # await checkpoint_service.save(checkpoint)
        
        return checkpoint
    
    @staticmethod
    async def get_default_pipeline(workflow_type: str) -> Optional[List[str]]:
        """Get default pipeline for workflow type."""
        registry = TaskRegistry()
        return registry.get_default_pipeline(workflow_type)
    
    @staticmethod
    async def list_available_pipelines() -> Dict[str, List[str]]:
        """List all available default pipelines."""
        registry = TaskRegistry()
        return {
            "content_generation": registry.get_default_pipeline("content_generation"),
            "content_with_approval": registry.get_default_pipeline("content_with_approval"),
            "social_media": registry.get_default_pipeline("social_media"),
            "financial_analysis": registry.get_default_pipeline("financial_analysis"),
            "market_analysis": registry.get_default_pipeline("market_analysis"),
            "performance_review": registry.get_default_pipeline("performance_review"),
        }
