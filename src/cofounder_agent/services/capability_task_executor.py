"""
Capability Task Executor - Executes tasks composed of chained capabilities.

A task is a sequence of capabilities where outputs of one step feed into
inputs of the next step (pipeline data flow).
"""

from typing import Any, Dict, List, Optional
from dataclasses import dataclass, field, asdict
from datetime import datetime
import uuid
import asyncio
import re

from .capability_registry import get_registry


@dataclass
class CapabilityStep:
    """A single step in a capability task."""
    capability_name: str
    inputs: Dict[str, Any]  # Can include references like "$step_0.output"
    output_key: str  # Key to store output under
    order: int = 0
    metadata: Dict[str, Any] = field(default_factory=dict)
    
    def to_dict(self) -> Dict[str, Any]:
        """Convert to dictionary."""
        return asdict(self)


@dataclass
class CapabilityTaskDefinition:
    """Definition of a capability-based task."""
    id: str = field(default_factory=lambda: str(uuid.uuid4()))
    name: str = ""
    description: str = ""
    steps: List[CapabilityStep] = field(default_factory=list)
    tags: List[str] = field(default_factory=list)
    created_at: datetime = field(default_factory=datetime.utcnow)
    owner_id: str = ""
    
    def to_dict(self) -> Dict[str, Any]:
        """Convert to dictionary."""
        return {
            "id": self.id,
            "name": self.name,
            "description": self.description,
            "steps": [step.to_dict() for step in self.steps],
            "tags": self.tags,
            "created_at": self.created_at.isoformat(),
            "owner_id": self.owner_id,
        }


@dataclass
class StepResult:
    """Result of executing a single capability step."""
    step_index: int
    capability_name: str
    output_key: str
    output: Any
    duration_ms: float
    error: Optional[str] = None
    status: str = "completed"  # completed, failed
    
    def to_dict(self) -> Dict[str, Any]:
        """Convert to dictionary."""
        return {
            "step_index": self.step_index,
            "capability_name": self.capability_name,
            "output_key": self.output_key,
            "output": self.output,
            "duration_ms": self.duration_ms,
            "error": self.error,
            "status": self.status,
        }


@dataclass
class TaskExecutionResult:
    """Result of executing a complete capability task."""
    task_id: str
    execution_id: str = field(default_factory=lambda: str(uuid.uuid4()))
    owner_id: str = ""
    status: str = "completed"  # pending, running, completed, failed
    step_results: List[StepResult] = field(default_factory=list)
    final_outputs: Dict[str, Any] = field(default_factory=dict)
    total_duration_ms: float = 0.0
    error: Optional[str] = None
    started_at: datetime = field(default_factory=datetime.utcnow)
    completed_at: Optional[datetime] = None
    
    def to_dict(self) -> Dict[str, Any]:
        """Convert to dictionary."""
        return {
            "task_id": self.task_id,
            "execution_id": self.execution_id,
            "owner_id": self.owner_id,
            "status": self.status,
            "step_results": [r.to_dict() for r in self.step_results],
            "final_outputs": self.final_outputs,
            "total_duration_ms": self.total_duration_ms,
            "error": self.error,
            "started_at": self.started_at.isoformat(),
            "completed_at": self.completed_at.isoformat() if self.completed_at else None,
        }
    
    @property
    def progress_percent(self) -> int:
        """Calculate progress percentage based on completed steps."""
        if not self.step_results:
            return 0
        completed = sum(1 for r in self.step_results if r.status == "completed")
        return int((completed / len(self.step_results)) * 100) if self.step_results else 0


class CapabilityTaskExecutor:
    """Executes capability-based tasks with data flow between steps."""
    
    def __init__(self, registry=None):
        """
        Initialize executor.
        
        Args:
            registry: CapabilityRegistry instance (defaults to global)
        """
        self.registry = registry or get_registry()
    
    def _resolve_input_reference(self, value: Any, context: Dict[str, Any]) -> Any:
        """
        Resolve input references (e.g., "$step_0.output" or "$research_data").
        
        Args:
            value: Value that may contain reference
            context: Execution context with available outputs
            
        Returns:
            Resolved value
        """
        if not isinstance(value, str):
            return value
        
        # Match patterns like $step_0.output or $research_data
        match = re.match(r'\$([a-zA-Z_][a-zA-Z0-9_]*)', value)
        if not match:
            return value
        
        key = match.group(1)
        if key in context:
            return context[key]
        
        # If not in context, return original value (may be literal string)
        return value
    
    def _resolve_inputs(
        self,
        inputs: Dict[str, Any],
        context: Dict[str, Any]
    ) -> Dict[str, Any]:
        """
        Resolve all input references in an inputs dict.
        
        Args:
            inputs: Raw inputs with possible references
            context: Execution context
            
        Returns:
            Resolved inputs
        """
        resolved = {}
        for key, value in inputs.items():
            resolved[key] = self._resolve_input_reference(value, context)
        return resolved
    
    async def execute(
        self,
        task: CapabilityTaskDefinition,
    ) -> TaskExecutionResult:
        """
        Execute a capability task.
        
        Args:
            task: CapabilityTaskDefinition to execute
            
        Returns:
            TaskExecutionResult with outputs and status
        """
        result = TaskExecutionResult(
            task_id=task.id,
            owner_id=task.owner_id,
            status="running",
        )
        
        # Sort steps by order
        sorted_steps = sorted(task.steps, key=lambda s: s.order)
        
        # Execution context (stores outputs from previous steps)
        context: Dict[str, Any] = {}
        
        import time
        start_time = time.time()
        
        try:
            for step_index, step in enumerate(sorted_steps):
                step_start = time.time()
                
                try:
                    # Resolve input references
                    resolved_inputs = self._resolve_inputs(step.inputs, context)
                    
                    # Execute capability
                    output = await self.registry.execute(
                        step.capability_name,
                        **resolved_inputs
                    )
                    
                    # Store output in context
                    context[step.output_key] = output
                    
                    # Record step result
                    step_duration = (time.time() - step_start) * 1000
                    step_result = StepResult(
                        step_index=step_index,
                        capability_name=step.capability_name,
                        output_key=step.output_key,
                        output=output,
                        duration_ms=step_duration,
                        status="completed",
                    )
                    result.step_results.append(step_result)
                    
                except Exception as e:
                    # Record failed step
                    step_duration = (time.time() - step_start) * 1000
                    step_result = StepResult(
                        step_index=step_index,
                        capability_name=step.capability_name,
                        output_key=step.output_key,
                        output=None,
                        duration_ms=step_duration,
                        error=str(e),
                        status="failed",
                    )
                    result.step_results.append(step_result)
                    
                    # Stop execution on first failure
                    result.error = f"Step {step_index} ({step.capability_name}) failed: {str(e)}"
                    result.status = "failed"
                    break
            
            # If all steps succeeded, mark as completed
            if result.status == "running":
                result.status = "completed"
                result.final_outputs = context.copy()
            
        except Exception as e:
            result.status = "failed"
            result.error = f"Task execution failed: {str(e)}"
        
        finally:
            result.total_duration_ms = (time.time() - start_time) * 1000
            result.completed_at = datetime.utcnow()
        
        return result
    
    async def execute_parallel_steps(
        self,
        task: CapabilityTaskDefinition,
        parallel_groups: Optional[List[List[int]]] = None,
    ) -> TaskExecutionResult:
        """
        Execute task with parallel step execution.
        
        Args:
            task: CapabilityTaskDefinition to execute
            parallel_groups: List of lists of step indices to run in parallel
                             Example: [[0, 1], [2], [3]] means run steps 0&1 in parallel,
                             then step 2, then step 3
            
        Returns:
            TaskExecutionResult with outputs and status
        """
        result = TaskExecutionResult(
            task_id=task.id,
            owner_id=task.owner_id,
            status="running",
        )
        
        # Default: each step runs serially
        if parallel_groups is None:
            sorted_steps = sorted(task.steps, key=lambda s: s.order)
            parallel_groups = [[i] for i in range(len(sorted_steps))]
        
        context: Dict[str, Any] = {}
        import time
        start_time = time.time()
        
        try:
            for group in parallel_groups:
                tasks_to_run = []
                group_steps = []
                
                for step_idx in group:
                    step = task.steps[step_idx]
                    group_steps.append((step_idx, step))
                    tasks_to_run.append(
                        self._execute_step(step, step_idx, context)
                    )
                
                # Run all steps in group in parallel
                step_results = await asyncio.gather(*tasks_to_run, return_exceptions=True)
                
                for (step_idx, step), step_result in zip(group_steps, step_results):
                    if isinstance(step_result, Exception):
                        # Failed
                        result.step_results.append(StepResult(
                            step_index=step_idx,
                            capability_name=step.capability_name,
                            output_key=step.output_key,
                            output=None,
                            duration_ms=0,
                            error=str(step_result),
                            status="failed",
                        ))
                        result.status = "failed"
                        result.error = f"Step {step_idx} failed: {str(step_result)}"
                        break
                    else:
                        # Success - update context
                        context[step.output_key] = step_result["output"]
                        result.step_results.append(step_result)
                
                if result.status == "failed":
                    break
            
            if result.status == "running":
                result.status = "completed"
                result.final_outputs = context.copy()
        
        except Exception as e:
            result.status = "failed"
            result.error = str(e)
        
        finally:
            result.total_duration_ms = (time.time() - start_time) * 1000
            result.completed_at = datetime.utcnow()
        
        return result
    
    async def _execute_step(
        self,
        step: CapabilityStep,
        step_idx: int,
        context: Dict[str, Any]
    ) -> Dict[str, Any]:
        """Execute a single step and return result dict."""
        import time
        step_start = time.time()
        
        resolved_inputs = self._resolve_inputs(step.inputs, context)
        output = await self.registry.execute(
            step.capability_name,
            **resolved_inputs
        )
        
        step_duration = (time.time() - step_start) * 1000
        return {
            "step_index": step_idx,
            "capability_name": step.capability_name,
            "output_key": step.output_key,
            "output": output,
            "duration_ms": step_duration,
            "status": "completed",
        }


# Convenience function
async def execute_capability_task(
    task: CapabilityTaskDefinition,
    parallel: bool = False,
) -> TaskExecutionResult:
    """
    Execute a capability task using default executor.
    
    Args:
        task: Task to execute
        parallel: Whether to attempt parallel execution
        
    Returns:
        TaskExecutionResult
    """
    executor = CapabilityTaskExecutor()
    return await executor.execute(task)
