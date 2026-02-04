"""Workflow request and response schemas for unified pipeline execution.

Defines the unified interface for all workflow requests (form, chat, voice)
and the response format for all workflow executions.
"""

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Dict, List, Optional

from src.cofounder_agent.tasks.base import TaskResult


@dataclass
class WorkflowRequest:
    """Unified request schema for all workflow types.

    Supports form submissions, natural language input, and voice commands.
    Single source of truth for all request types.

    Attributes:
        workflow_type: Type of workflow ('content_generation', 'social_campaign', etc.)
        input_data: Task-specific input data
        user_id: User executing the workflow
        custom_pipeline: Override default pipeline with custom task sequence (optional)
        execution_options: Runtime configuration (timeout, retry, etc.)
        source: Where request originated ('form', 'chat', 'voice', 'api')
        workflow_id: Optional pre-assigned workflow ID (for resuming paused workflows)
        request_id: Unique request identifier for tracing
    """

    workflow_type: str
    input_data: Dict[str, Any]
    user_id: str
    source: str = "api"
    custom_pipeline: Optional[List[str]] = None
    execution_options: Dict[str, Any] = field(
        default_factory=lambda: {
            "timeout": 300,
            "max_retries": 3,
            "fail_on_error": False,
            "skip_on_error": False,
        }
    )
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

        # Auto-generate IDs if not provided
        if not self.request_id:
            import uuid

            self.request_id = str(uuid.uuid4())
        if not self.workflow_id and self.source != "resume":
            import uuid

            self.workflow_id = str(uuid.uuid4())

    @property
    def is_custom_pipeline(self) -> bool:
        """Check if request specifies a custom pipeline."""
        return self.custom_pipeline is not None and len(self.custom_pipeline) > 0


@dataclass
class WorkflowResponse:
    """Unified response schema for all workflow executions.

    Provides consistent format for all workflow results regardless
    of pipeline type or task composition.

    Attributes:
        workflow_id: Unique workflow identifier
        workflow_type: Type of workflow executed
        status: Overall workflow status (COMPLETED, FAILED, PENDING, AWAITING_INPUT)
        user_id: User who executed workflow
        output: Final workflow output
        task_results: Results from all executed tasks
        errors: Any errors that occurred
        start_time: When execution started
        end_time: When execution completed
        duration_seconds: Total execution time
        task_count: Number of tasks executed
        execution_metadata: Additional metadata
    """

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
            "task_results": [
                {
                    "task_id": tr.task_id,
                    "task_name": tr.task_name,
                    "status": tr.status,
                    "output": tr.output,
                    "error": tr.error,
                    "duration_seconds": tr.duration_seconds,
                }
                for tr in self.task_results
            ],
            "start_time": self.start_time.isoformat(),
            "end_time": self.end_time.isoformat(),
            "duration_seconds": self.duration_seconds,
            "task_count": self.task_count,
            "errors": self.errors,
            "execution_metadata": self.execution_metadata,
        }


@dataclass
class WorkflowCheckpoint:
    """Checkpoint for paused/approval workflows.

    Stores workflow execution state for later resumption,
    particularly useful for approval gate workflows.

    Attributes:
        workflow_id: Workflow being paused
        task_index: Which task in pipeline we're paused at
        accumulated_data: Data from all completed tasks
        pending_approval: Data awaiting approval
        pending_actions: Required actions (approve, reject, etc.)
        created_at: When checkpoint was created
        expires_at: When checkpoint expires (optional)
    """

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


@dataclass
class WorkflowApprovalRequest:
    """Request to approve or reject a workflow checkpoint.

    Attributes:
        workflow_id: Workflow to approve/reject
        action: 'approve' or 'reject'
        user_id: User approving
        comment: Optional approval comment
        modifications: Optional modifications to pending approval data
    """

    workflow_id: str
    action: str  # 'approve' or 'reject'
    user_id: str
    comment: Optional[str] = None
    modifications: Optional[Dict[str, Any]] = None

    def __post_init__(self):
        """Validate approval request."""
        if self.action not in ["approve", "reject"]:
            raise ValueError("action must be 'approve' or 'reject'")
        if not self.workflow_id or not self.user_id:
            raise ValueError("workflow_id and user_id are required")
