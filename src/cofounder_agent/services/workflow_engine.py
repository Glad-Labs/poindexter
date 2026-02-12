"""
Workflow Engine - Orchestrates task execution with retry logic, status tracking, and result management

Handles:
- Phase-based workflow execution
- Automatic retry with exponential backoff
- Status tracking (pending, running, completed, failed)
- Result accumulation across phases
- Error handling and recovery
- Quality feedback integration
- Training data collection

Architecture:
- WorkflowPhase: Defines a phase name, service/task, and execution parameters
- WorkflowContext: Manages state, results, and metadata during execution
- WorkflowEngine: Orchestrates phase execution with comprehensive error handling
- PhaseResult: Encapsulates phase output with metadata (duration, status, errors)
"""

import asyncio
import json
import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Any, Callable, Dict, List, Optional

logger = logging.getLogger(__name__)


class PhaseStatus(str, Enum):
    """Execution status of a phase"""

    PENDING = "pending"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"
    SKIPPED = "skipped"
    RETRY = "retry"


class WorkflowStatus(str, Enum):
    """Overall workflow execution status"""

    PENDING = "pending"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"
    PAUSED = "paused"
    CANCELLED = "cancelled"


@dataclass
class WorkflowPhase:
    """Definition of a workflow phase"""

    name: str
    """Phase name (e.g., 'research', 'draft', 'refine', 'assess', 'finalize')"""

    handler: Callable
    """Async callable that executes the phase. Signature: async def handler(context, **kwargs) -> PhaseResult"""

    description: str = ""
    """Human-readable phase description"""

    timeout_seconds: int = 300
    """Maximum time this phase can run before timeout"""

    max_retries: int = 3
    """Maximum retry attempts on failure"""

    skip_on_error: bool = False
    """Whether to skip this phase if previous phase fails"""

    required: bool = True
    """If False, workflow can complete even if this phase fails"""

    metadata: Dict[str, Any] = field(default_factory=dict)
    """Additional metadata for this phase"""


@dataclass
class PhaseResult:
    """Result of executing a single phase"""

    phase_name: str
    status: PhaseStatus
    output: Any = None
    error: Optional[str] = None
    duration_ms: float = 0.0
    retry_count: int = 0
    started_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    completed_at: Optional[datetime] = None
    metadata: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        """Serialize to dictionary"""
        return {
            "phase_name": self.phase_name,
            "status": self.status.value,
            "output": self.output,
            "error": self.error,
            "duration_ms": self.duration_ms,
            "retry_count": self.retry_count,
            "started_at": self.started_at.isoformat(),
            "completed_at": self.completed_at.isoformat() if self.completed_at else None,
            "metadata": self.metadata,
        }


@dataclass
class WorkflowContext:
    """Context and state for workflow execution"""

    workflow_id: str
    """Unique workflow ID"""

    request_id: str
    """Original request ID"""

    initial_input: Any
    """Initial input data for the workflow"""

    started_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    """When the workflow started"""

    phases_executed: List[str] = field(default_factory=list)
    """Names of phases that have been executed"""

    current_phase: Optional[str] = None
    """Currently executing phase name"""

    results: Dict[str, PhaseResult] = field(default_factory=dict)
    """Results from each phase (phase_name -> PhaseResult)"""

    accumulated_output: Any = None
    """Accumulated output passed between phases"""

    status: WorkflowStatus = WorkflowStatus.PENDING
    """Overall workflow status"""

    variables: Dict[str, Any] = field(default_factory=dict)
    """Workflow variables that can be set during execution"""

    tags: List[str] = field(default_factory=list)
    """Tags for categorization and filtering"""

    def get_phase_result(self, phase_name: str) -> Optional[PhaseResult]:
        """Get result from a previously executed phase"""
        return self.results.get(phase_name)

    def set_variable(self, key: str, value: Any) -> None:
        """Set a workflow variable for inter-phase communication"""
        self.variables[key] = value

    def get_variable(self, key: str, default: Any = None) -> Any:
        """Get a workflow variable"""
        return self.variables.get(key, default)

    def has_failures(self, include_skipped: bool = False) -> bool:
        """Check if any phases have failed"""
        for result in self.results.values():
            if result.status == PhaseStatus.FAILED:
                return True
            if include_skipped and result.status == PhaseStatus.SKIPPED:
                return True
        return False

    def to_dict(self) -> Dict[str, Any]:
        """Serialize to dictionary"""
        return {
            "workflow_id": self.workflow_id,
            "request_id": self.request_id,
            "started_at": self.started_at.isoformat(),
            "current_phase": self.current_phase,
            "status": self.status.value,
            "phases_executed": self.phases_executed,
            "results": {name: result.to_dict() for name, result in self.results.items()},
            "variables": self.variables,
            "tags": self.tags,
        }


class WorkflowEngine:
    """
    Orchestrates workflow execution with comprehensive error handling and retry logic.

    Features:
    - Phase-based execution with flexible ordering
    - Automatic retry with exponential backoff
    - Timeout enforcement per phase
    - Status tracking and result accumulation
    - Error recovery and fallback options
    - Quality feedback integration
    - Training data collection

    Example:
        ```python
        engine = WorkflowEngine(database_service=db_service)

        # Define phases
        phases = [
            WorkflowPhase(
                name="research",
                handler=async_research_handler,
                timeout_seconds=180,
                max_retries=2
            ),
            WorkflowPhase(
                name="draft",
                handler=async_draft_handler,
                timeout_seconds=300,
                max_retries=3
            ),
            WorkflowPhase(
                name="assess",
                handler=async_assessment_handler,
                timeout_seconds=120,
                max_retries=2,
                skip_on_error=True
            ),
        ]

        # Execute workflow
        context = WorkflowContext(
            workflow_id=str(uuid.uuid4()),
            request_id=request_id,
            initial_input={"topic": "AI", "keywords": ["machine learning"]},
            tags=["blog", "technical"]
        )

        result = await engine.execute_workflow(phases, context)
        print(f"Workflow status: {result.status}")
        print(f"Total duration: {result.to_dict()['duration_ms']}ms")
        ```
    """

    def __init__(
        self,
        database_service: Optional[Any] = None,
        quality_service: Optional[Any] = None,
        error_handler: Optional[Callable] = None,
        enable_training_data: bool = True,
    ):
        """
        Initialize workflow engine.

        Args:
            database_service: Optional database service for persistence
            quality_service: Optional quality service for feedback integration
            error_handler: Optional custom error handler callable
            enable_training_data: Whether to collect training data from executions
        """
        self.database_service = database_service
        self.quality_service = quality_service
        self.error_handler = error_handler
        self.enable_training_data = enable_training_data
        self.executed_workflows: Dict[str, WorkflowContext] = {}

        logger.info("WorkflowEngine initialized")

    async def execute_workflow(
        self, phases: List[WorkflowPhase], context: WorkflowContext
    ) -> WorkflowContext:
        """
        Execute a workflow with multiple phases.

        Args:
            phases: List of WorkflowPhase definitions
            context: WorkflowContext managing execution state

        Returns:
            Updated WorkflowContext with results and status
        """
        context.status = WorkflowStatus.RUNNING
        start_time = asyncio.get_event_loop().time()

        logger.info(
            "[%s] Starting workflow with %d phases: %s",
            context.workflow_id,
            len(phases),
            ", ".join(p.name for p in phases),
        )

        for phase in phases:
            if context.status in (WorkflowStatus.CANCELLED, WorkflowStatus.PAUSED):
                logger.info("[%s] Workflow paused or cancelled, stopping", context.workflow_id)
                break

            phase_result = await self._execute_phase(phase, context)

            # Check if we should continue
            if phase_result.status == PhaseStatus.FAILED:
                if phase.required:
                    logger.error(
                        "[%s] Required phase '%s' failed, stopping workflow",
                        context.workflow_id,
                        phase.name,
                    )
                    context.status = WorkflowStatus.FAILED
                    break
                else:
                    logger.warning(
                        "[%s] Optional phase '%s' failed, continuing",
                        context.workflow_id,
                        phase.name,
                    )

        # Mark as completed
        duration_ms = (asyncio.get_event_loop().time() - start_time) * 1000

        if context.status == WorkflowStatus.RUNNING:
            if context.has_failures():
                context.status = WorkflowStatus.FAILED
            else:
                context.status = WorkflowStatus.COMPLETED

        logger.info(
            "[%s] Workflow completed with status: %s (duration: %.0fms)",
            context.workflow_id,
            context.status.value,
            duration_ms,
        )

        # Store in memory
        self.executed_workflows[context.workflow_id] = context

        # Optionally persist
        if self.database_service and self.enable_training_data:
            await self._store_workflow_result(context, duration_ms)

        return context

    async def _execute_phase(
        self, phase: WorkflowPhase, context: WorkflowContext
    ) -> PhaseResult:
        """
        Execute a single phase with retry logic and error handling.

        Args:
            phase: WorkflowPhase definition
            context: WorkflowContext managing execution state

        Returns:
            PhaseResult with execution details
        """
        context.current_phase = phase.name
        result = PhaseResult(phase_name=phase.name, status=PhaseStatus.PENDING)
        start_time = asyncio.get_event_loop().time()

        logger.info("[%s] Starting phase: %s", context.workflow_id, phase.name)

        for attempt in range(phase.max_retries + 1):
            try:
                result.status = PhaseStatus.RUNNING
                result.retry_count = attempt

                # Execute phase handler with timeout
                logger.debug(
                    "[%s] Executing phase '%s' (attempt %d/%d)",
                    context.workflow_id,
                    phase.name,
                    attempt + 1,
                    phase.max_retries + 1,
                )

                try:
                    phase_output = await asyncio.wait_for(
                        phase.handler(context),
                        timeout=phase.timeout_seconds,
                    )

                    result.output = phase_output
                    result.status = PhaseStatus.COMPLETED
                    result.completed_at = datetime.now(timezone.utc)

                    logger.info(
                        "[%s] Phase '%s' completed successfully",
                        context.workflow_id,
                        phase.name,
                    )

                    # Update context
                    context.accumulated_output = phase_output
                    context.phases_executed.append(phase.name)
                    context.results[phase.name] = result

                    # Set duration
                    result.duration_ms = (asyncio.get_event_loop().time() - start_time) * 1000

                    return result

                except asyncio.TimeoutError:
                    raise TimeoutError(
                        f"Phase '{phase.name}' exceeded {phase.timeout_seconds}s timeout"
                    )

            except Exception as e:
                result.duration_ms = (asyncio.get_event_loop().time() - start_time) * 1000
                result.error = f"{type(e).__name__}: {str(e)}"

                if attempt < phase.max_retries:
                    # Calculate exponential backoff: 2^attempt seconds
                    wait_time = 2**attempt
                    logger.warning(
                        "[%s] Phase '%s' failed (attempt %d/%d): %s. Retrying in %ds...",
                        context.workflow_id,
                        phase.name,
                        attempt + 1,
                        phase.max_retries + 1,
                        result.error,
                        wait_time,
                    )
                    await asyncio.sleep(wait_time)
                    result.status = PhaseStatus.RETRY
                else:
                    logger.error(
                        "[%s] Phase '%s' failed after %d attempts: %s",
                        context.workflow_id,
                        phase.name,
                        phase.max_retries + 1,
                        result.error,
                    )
                    result.status = PhaseStatus.FAILED
                    result.completed_at = datetime.now(timezone.utc)

                    # Call custom error handler if provided
                    if self.error_handler:
                        try:
                            await self.error_handler(context, phase, result)
                        except Exception as handler_error:
                            logger.error(
                                "[%s] Error handler failed: %s",
                                context.workflow_id,
                                handler_error,
                            )

                    # Update context
                    context.results[phase.name] = result
                    return result

        # Should not reach here, but return failed result if we do
        result.status = PhaseStatus.FAILED
        result.completed_at = datetime.now(timezone.utc)
        context.results[phase.name] = result
        return result

    def pause_workflow(self, workflow_id: str) -> bool:
        """Pause a currently executing workflow"""
        if workflow_id in self.executed_workflows:
            context = self.executed_workflows[workflow_id]
            if context.status == WorkflowStatus.RUNNING:
                context.status = WorkflowStatus.PAUSED
                logger.info("[%s] Workflow paused", workflow_id)
                return True
        return False

    def resume_workflow(self, workflow_id: str) -> bool:
        """Resume a paused workflow"""
        if workflow_id in self.executed_workflows:
            context = self.executed_workflows[workflow_id]
            if context.status == WorkflowStatus.PAUSED:
                context.status = WorkflowStatus.RUNNING
                logger.info("[%s] Workflow resumed", workflow_id)
                return True
        return False

    def cancel_workflow(self, workflow_id: str) -> bool:
        """Cancel a workflow"""
        if workflow_id in self.executed_workflows:
            context = self.executed_workflows[workflow_id]
            if context.status in (WorkflowStatus.RUNNING, WorkflowStatus.PAUSED):
                context.status = WorkflowStatus.CANCELLED
                logger.info("[%s] Workflow cancelled", workflow_id)
                return True
        return False

    def get_workflow_status(self, workflow_id: str) -> Optional[Dict[str, Any]]:
        """Get current status of a workflow"""
        if workflow_id in self.executed_workflows:
            return self.executed_workflows[workflow_id].to_dict()
        return None

    async def _store_workflow_result(
        self, context: WorkflowContext, total_duration_ms: float
    ) -> None:
        """Store workflow execution result for training data"""
        try:
            if not self.database_service:
                return

            workflow_data = {
                "workflow_id": context.workflow_id,
                "request_id": context.request_id,
                "status": context.status.value,
                "duration_ms": total_duration_ms,
                "phases_executed": context.phases_executed,
                "has_failures": context.has_failures(),
                "started_at": context.started_at.isoformat(),
                "results": {name: result.to_dict() for name, result in context.results.items()},
            }

            logger.debug(
                "[%s] Storing workflow result: %s",
                context.workflow_id,
                json.dumps(workflow_data, indent=2)[:500],
            )

            # TODO: Implement persistent storage via database_service
            # This would store results for training data collection and analysis

        except Exception as e:
            logger.warning("[%s] Failed to store workflow result: %s", context.workflow_id, e)

    async def execute_phase_with_quality_feedback(
        self,
        phase: WorkflowPhase,
        context: WorkflowContext,
        quality_threshold: float = 0.7,
        max_quality_retries: int = 3,
    ) -> PhaseResult:
        """
        Execute a phase with quality assessment and refinement loop.

        Args:
            phase: WorkflowPhase to execute
            context: WorkflowContext managing execution state
            quality_threshold: Minimum quality score (0-1) to accept result
            max_quality_retries: Maximum refinement attempts

        Returns:
            PhaseResult with quality assessment metadata
        """
        result = await self._execute_phase(phase, context)

        if result.status != PhaseStatus.COMPLETED or not self.quality_service:
            return result

        # Assessment and refinement loop
        for refinement_attempt in range(max_quality_retries):
            try:
                # Assess quality of current output
                quality_score = await self.quality_service.assess_quality(result.output)
                result.metadata["quality_score"] = quality_score
                result.metadata["refinement_attempt"] = refinement_attempt

                logger.info(
                    "[%s] Phase '%s' quality: %.2f (threshold: %.2f)",
                    context.workflow_id,
                    phase.name,
                    quality_score,
                    quality_threshold,
                )

                if quality_score >= quality_threshold:
                    logger.info("[%s] Phase '%s' meets quality threshold", context.workflow_id, phase.name)
                    break

                if refinement_attempt < max_quality_retries - 1:
                    logger.info(
                        "[%s] Phase '%s' below threshold, refining (attempt %d/%d)",
                        context.workflow_id,
                        phase.name,
                        refinement_attempt + 1,
                        max_quality_retries,
                    )

                    # Re-execute phase for refinement
                    refinement_result = await self._execute_phase(phase, context)
                    result.output = refinement_result.output
                    result.retry_count += 1

            except Exception as e:
                logger.warning(
                    "[%s] Quality assessment failed for phase '%s': %s",
                    context.workflow_id,
                    phase.name,
                    e,
                )
                # Continue with current result even if quality assessment fails

        return result
