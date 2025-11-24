"""Base Task class and execution context for all workflow tasks."""

from abc import ABC, abstractmethod
from enum import Enum
from typing import Any, Dict, Optional, List
from dataclasses import dataclass, field
from datetime import datetime
import uuid
import logging

logger = logging.getLogger(__name__)


class TaskStatus(str, Enum):
    """Task execution status."""
    PENDING = "pending"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"
    SKIPPED = "skipped"
    AWAITING_INPUT = "awaiting_input"


@dataclass
class TaskResult:
    """Result of task execution."""
    status: TaskStatus
    task_id: str
    task_name: str
    output: Dict[str, Any]
    error: Optional[str] = None
    duration_seconds: Optional[float] = None
    started_at: Optional[datetime] = None
    completed_at: Optional[datetime] = None
    metadata: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        """Convert to dictionary for JSON serialization."""
        return {
            "status": self.status.value,
            "task_id": self.task_id,
            "task_name": self.task_name,
            "output": self.output,
            "error": self.error,
            "duration_seconds": self.duration_seconds,
            "started_at": self.started_at.isoformat() if self.started_at else None,
            "completed_at": self.completed_at.isoformat() if self.completed_at else None,
            "metadata": self.metadata,
        }


@dataclass
class ExecutionContext:
    """Context passed through task execution chain."""
    workflow_id: str
    user_id: str
    workflow_type: str
    execution_start: datetime
    task_history: List[TaskResult] = field(default_factory=list)
    workflow_data: Dict[str, Any] = field(default_factory=dict)  # Accumulates across tasks
    execution_options: Dict[str, Any] = field(default_factory=dict)
    
    def add_task_result(self, result: TaskResult) -> None:
        """Record task execution result."""
        self.task_history.append(result)
        logger.info(
            f"Task {result.task_name} completed with status {result.status.value}",
            extra={"workflow_id": self.workflow_id, "task_id": result.task_id}
        )
    
    def get_task_result(self, task_name: str) -> Optional[TaskResult]:
        """Retrieve previous task result by name."""
        for result in self.task_history:
            if result.task_name == task_name:
                return result
        return None
    
    def get_latest_output(self) -> Dict[str, Any]:
        """Get output from last completed task."""
        if self.task_history:
            return self.task_history[-1].output
        return {}
    
    def merge_workflow_data(self, data: Dict[str, Any]) -> None:
        """Merge new data into workflow context."""
        self.workflow_data.update(data)


class Task(ABC):
    """
    Base class for all workflow tasks.
    
    All tasks inherit from this class and implement the execute() method.
    Tasks are pure functions that transform input to output.
    """

    def __init__(self, name: str, description: str = ""):
        """
        Initialize task.
        
        Args:
            name: Unique task identifier (e.g., "research", "creative")
            description: Human-readable description of task
        """
        self.name = name
        self.description = description
        self.task_id = str(uuid.uuid4())
        self.logger = logging.getLogger(f"Task.{name}")

    @abstractmethod
    async def execute(
        self,
        input_data: Dict[str, Any],
        context: ExecutionContext,
    ) -> TaskResult:
        """
        Execute task with given input.
        
        Args:
            input_data: Task input data (output of previous task + initial input)
            context: Execution context with workflow data and history
        
        Returns:
            TaskResult with status, output, and metadata
        
        This method should:
        1. Validate input_data
        2. Perform task logic
        3. Return TaskResult with clear status and output
        4. Handle errors gracefully
        5. Not raise exceptions (wrap in TaskResult)
        """
        pass

    def __repr__(self) -> str:
        """String representation."""
        return f"<Task: {self.name}>"


class PureTask(Task):
    """
    Extended Task class with built-in error handling and logging.
    
    Handles common patterns:
    - Task validation
    - Error wrapping
    - Logging
    - Timing
    - LLM calls with model fallback
    """

    def __init__(
        self,
        name: str,
        description: str = "",
        required_inputs: Optional[List[str]] = None,
        timeout_seconds: int = 300,
    ):
        """
        Initialize pure task.
        
        Args:
            name: Task identifier
            description: Task description
            required_inputs: List of required input fields
            timeout_seconds: Task execution timeout
        """
        super().__init__(name, description)
        self.required_inputs = required_inputs or []
        self.timeout_seconds = timeout_seconds

    def _validate_input(self, input_data: Dict[str, Any]) -> Optional[str]:
        """
        Validate task input.
        
        Returns:
            Error message if validation fails, None if valid
        """
        for field in self.required_inputs:
            if field not in input_data or input_data[field] is None:
                return f"Missing required field: {field}"
        return None

    async def execute(
        self,
        input_data: Dict[str, Any],
        context: ExecutionContext,
    ) -> TaskResult:
        """
        Execute task with error handling and logging.
        
        Validates input, calls _execute_internal(), handles errors.
        """
        import time
        
        started_at = datetime.now()
        start_time = time.time()

        try:
            # Validate input
            validation_error = self._validate_input(input_data)
            if validation_error:
                return TaskResult(
                    status=TaskStatus.FAILED,
                    task_id=self.task_id,
                    task_name=self.name,
                    output={},
                    error=validation_error,
                    started_at=started_at,
                    completed_at=datetime.now(),
                )

            # Execute task logic
            self.logger.info(f"Starting task execution", extra={"workflow_id": context.workflow_id})
            output = await self._execute_internal(input_data, context)

            # Success
            completed_at = datetime.now()
            duration = time.time() - start_time

            result = TaskResult(
                status=TaskStatus.COMPLETED,
                task_id=self.task_id,
                task_name=self.name,
                output=output,
                started_at=started_at,
                completed_at=completed_at,
                duration_seconds=duration,
            )

            self.logger.info(
                f"Task completed successfully in {duration:.2f}s",
                extra={"workflow_id": context.workflow_id}
            )

            return result

        except Exception as e:
            # Error handling
            self.logger.error(
                f"Task execution failed: {str(e)}",
                exc_info=True,
                extra={"workflow_id": context.workflow_id}
            )
            
            return TaskResult(
                status=TaskStatus.FAILED,
                task_id=self.task_id,
                task_name=self.name,
                output={},
                error=str(e),
                started_at=started_at,
                completed_at=datetime.now(),
                duration_seconds=time.time() - start_time,
            )

    @abstractmethod
    async def _execute_internal(
        self,
        input_data: Dict[str, Any],
        context: ExecutionContext,
    ) -> Dict[str, Any]:
        """
        Internal task execution logic.
        
        Implement this method instead of execute() for automatic
        error handling, logging, and validation.
        
        Args:
            input_data: Validated task input
            context: Execution context
        
        Returns:
            Output dictionary to pass to next task
        """
        pass
