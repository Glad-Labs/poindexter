"""
Shared data types for the unified orchestrator subsystem.

These types are intentionally kept in their own module to break the
circular-import chain that would otherwise arise between
unified_orchestrator, request_router, and any other module that needs
to reference Request / ExecutionResult without pulling in the full
orchestrator class.

Consumers should import directly from this module:

    from services.orchestrator_types import RequestType, Request, ExecutionResult

For backward compatibility unified_orchestrator.py re-exports all names
from this module, so existing code importing from there continues to work.
"""

from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Any, Dict, Optional


class RequestType(str, Enum):
    """High-level request types for routing."""

    CONTENT_CREATION = "content_creation"  # Blog posts, articles, copy
    CONTENT_SUBTASK = "content_subtask"  # Research, creative, QA, format individually
    FINANCIAL_ANALYSIS = "financial_analysis"
    COMPLIANCE_CHECK = "compliance_check"
    TASK_MANAGEMENT = "task_management"  # Create/manage tasks
    INFORMATION_RETRIEVAL = "information_retrieval"  # Look up data, show results
    DECISION_SUPPORT = "decision_support"  # "What should I..."
    SYSTEM_OPERATION = "system_operation"  # Status, health, help
    INTERVENTION = "intervention"  # Manual override, stop, etc.


class ExecutionStatus(str, Enum):
    """Status of request execution."""

    PENDING = "pending"
    PLANNING = "planning"
    EXECUTING = "executing"
    ASSESSING = "assessing"
    REFINEMENT = "refinement"
    PENDING_APPROVAL = "pending_approval"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"


@dataclass
class Request:
    """Unified request object passed between orchestrator components."""

    request_id: str
    original_text: str
    request_type: RequestType
    extracted_intent: str
    parameters: Dict[str, Any] = field(default_factory=dict)
    context: Dict[str, Any] = field(default_factory=dict)
    created_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    user_id: Optional[str] = None  # User ID from auth context


@dataclass
class ExecutionContext:
    """Context for execution — carries service references into handler methods."""

    request_id: str
    request_type: RequestType
    database_service: Any = None
    model_router: Any = None
    orchestrator_agents: Dict[str, Any] = field(default_factory=dict)
    quality_service: Any = None
    memory_system: Any = None


@dataclass
class ExecutionResult:
    """Result of executing a request."""

    request_id: str
    request_type: RequestType
    status: ExecutionStatus

    # Result data
    output: Any  # Content, analysis, decision, etc.
    task_id: Optional[str] = None  # For content tasks

    # Quality metrics
    quality_score: Optional[float] = None
    passed_quality: Optional[bool] = None
    feedback: Optional[str] = None

    # Execution details
    duration_ms: float = 0
    cost_usd: float = 0.0
    refinement_attempts: int = 0

    # Training data
    training_example: Optional[Dict[str, Any]] = None  # For model improvement

    # Metadata
    metadata: Dict[str, Any] = field(default_factory=dict)
    created_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))

    def to_dict(self) -> Dict[str, Any]:
        """Convert to dictionary for storage."""
        return {
            "request_id": self.request_id,
            "request_type": self.request_type.value,
            "status": self.status.value,
            "output": self.output,
            "task_id": self.task_id,
            "quality_score": self.quality_score,
            "passed_quality": self.passed_quality,
            "feedback": self.feedback,
            "duration_ms": self.duration_ms,
            "cost_usd": self.cost_usd,
            "refinement_attempts": self.refinement_attempts,
            "training_example": self.training_example,
            "metadata": self.metadata,
            "created_at": self.created_at.isoformat(),
        }
