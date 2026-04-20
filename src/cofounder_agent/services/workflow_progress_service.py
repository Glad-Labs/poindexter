"""
Workflow Progress Service - Real-time progress tracking for workflow executions

Provides progress tracking and callbacks for streaming workflow execution
progress to WebSocket clients in real-time.
"""

from collections.abc import Callable
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from typing import Any

from services.logger_config import get_logger

logger = get_logger(__name__)


@dataclass
class WorkflowProgress:
    """Represents the current state of a workflow execution"""

    execution_id: str
    workflow_id: str | None = None
    template: str | None = None
    status: str = "pending"  # pending, executing, completed, failed
    current_phase: int = 0  # 0-based index
    total_phases: int = 0
    phase_name: str = ""  # Name of current phase
    phase_status: str = ""  # pending, executing, completed, failed
    progress_percent: float = 0.0
    completed_phases: int = 0
    elapsed_time: float = 0.0
    estimated_remaining: float = 0.0
    message: str = ""
    error: str | None = None
    phase_results: dict[str, Any] = field(default_factory=dict)
    timestamp: str = ""

    def __post_init__(self):
        if not self.timestamp:
            self.timestamp = datetime.now(timezone.utc).isoformat()

    def to_dict(self) -> dict[str, Any]:
        """Convert to dictionary for JSON serialization"""
        return asdict(self)


class WorkflowProgressService:
    """Manages progress tracking for workflow executions"""

    def __init__(self):
        # Store progress by execution_id: {execution_id -> WorkflowProgress}
        self._progress: dict[str, WorkflowProgress] = {}
        # Store callbacks by execution_id: {execution_id -> [callback1, callback2, ...]}
        self._callbacks: dict[str, list[Callable]] = {}

    def create_progress(
        self,
        execution_id: str,
        workflow_id: str | None = None,
        template: str | None = None,
        total_phases: int = 0,
    ) -> WorkflowProgress:
        """Initialize progress tracking for a new workflow execution"""
        progress = WorkflowProgress(
            execution_id=execution_id,
            workflow_id=workflow_id,
            template=template,
            status="pending",
            total_phases=total_phases,
            message="Initializing workflow execution...",
        )
        self._progress[execution_id] = progress
        logger.info(
            "Created progress tracker for execution %s (%d phases)", execution_id, total_phases
        )
        return progress

    def get_progress(self, execution_id: str) -> WorkflowProgress | None:
        """Get current progress for an execution"""
        return self._progress.get(execution_id)

    def start_execution(
        self, execution_id: str, message: str = "Starting workflow execution..."
    ) -> WorkflowProgress:
        """Mark workflow execution as started"""
        progress = self._progress.get(execution_id)
        if not progress:
            progress = self.create_progress(execution_id)

        progress.status = "executing"
        progress.message = message
        progress.timestamp = datetime.now(timezone.utc).isoformat()

        self._notify_callbacks(execution_id, progress)
        return progress

    def start_phase(
        self,
        execution_id: str,
        phase_index: int,
        phase_name: str,
        message: str | None = None,
    ) -> WorkflowProgress | None:
        """Mark phase execution start"""
        progress = self._progress.get(execution_id)
        if not progress:
            return progress  # Execution not found

        progress.status = "executing"
        progress.current_phase = phase_index
        progress.phase_name = phase_name
        progress.phase_status = "executing"

        if message:
            progress.message = message
        else:
            progress.message = (
                f"Executing phase {phase_index + 1}/{progress.total_phases}: {phase_name}"
            )

        progress.timestamp = datetime.now(timezone.utc).isoformat()

        self._notify_callbacks(execution_id, progress)
        return progress

    def complete_phase(
        self,
        execution_id: str,
        phase_name: str,
        phase_output: dict[str, Any] | None = None,
        duration_ms: float | None = None,
    ) -> WorkflowProgress | None:
        """Mark phase as completed"""
        progress = self._progress.get(execution_id)
        if not progress:
            return progress  # Execution not found

        progress.completed_phases += 1
        progress.phase_status = "completed"

        # Store phase result
        if phase_output:
            progress.phase_results[phase_name] = {
                "status": "completed",
                "duration_ms": duration_ms or 0,
                "output": phase_output,
            }

        # Calculate progress percentage
        if progress.total_phases > 0:
            progress.progress_percent = (progress.completed_phases / progress.total_phases) * 100

        progress.message = (
            f"Completed phase {progress.completed_phases}/{progress.total_phases}: {phase_name}"
        )
        progress.timestamp = datetime.now(timezone.utc).isoformat()

        self._notify_callbacks(execution_id, progress)
        return progress

    def fail_phase(
        self,
        execution_id: str,
        phase_name: str,
        error: str,
    ) -> WorkflowProgress | None:
        """Mark phase as failed"""
        progress = self._progress.get(execution_id)
        if not progress:
            return progress  # Execution not found

        progress.phase_status = "failed"

        # Store phase result
        progress.phase_results[phase_name] = {
            "status": "failed",
            "error": error,
        }

        progress.message = f"Phase failed: {phase_name} - {error}"
        progress.timestamp = datetime.now(timezone.utc).isoformat()

        self._notify_callbacks(execution_id, progress)
        return progress

    def mark_complete(
        self,
        execution_id: str,
        final_output: dict[str, Any] | None = None,
        duration_ms: float | None = None,
        message: str = "Workflow execution completed",
    ) -> WorkflowProgress | None:
        """Mark workflow execution as completed"""
        progress = self._progress.get(execution_id)
        if not progress:
            return progress  # Execution not found

        progress.status = "completed"
        progress.phase_status = "completed"
        progress.progress_percent = 100.0
        progress.elapsed_time = duration_ms or 0.0
        progress.estimated_remaining = 0.0
        progress.message = message
        progress.timestamp = datetime.now(timezone.utc).isoformat()

        # Store final output in phase results
        if final_output:
            progress.phase_results["final_output"] = final_output

        self._notify_callbacks(execution_id, progress)
        return progress

    def mark_failed(
        self,
        execution_id: str,
        error: str,
        failed_phase: str | None = None,
    ) -> WorkflowProgress | None:
        """Mark workflow execution as failed"""
        progress = self._progress.get(execution_id)
        if not progress:
            return progress  # Execution not found

        progress.status = "failed"
        progress.phase_status = "failed"
        progress.error = error
        progress.message = (
            f"Workflow failed at phase {failed_phase}: {error}"
            if failed_phase
            else f"Workflow failed: {error}"
        )
        progress.timestamp = datetime.now(timezone.utc).isoformat()

        self._notify_callbacks(execution_id, progress)
        return progress

    def update_elapsed_time(
        self,
        execution_id: str,
        elapsed_time: float,
    ) -> WorkflowProgress | None:
        """Update elapsed time for execution"""
        progress = self._progress.get(execution_id)
        if not progress:
            return progress

        progress.elapsed_time = elapsed_time

        # Estimate remaining time based on progress
        if progress.progress_percent > 0 and progress.progress_percent < 100:
            time_per_percent = elapsed_time / progress.progress_percent
            remaining_percent = 100 - progress.progress_percent
            progress.estimated_remaining = time_per_percent * remaining_percent

        progress.timestamp = datetime.now(timezone.utc).isoformat()
        self._notify_callbacks(execution_id, progress)
        return progress

    def register_callback(
        self,
        execution_id: str,
        callback: Callable[[WorkflowProgress], Any],
    ) -> None:
        """Register a callback to be called when progress is updated"""
        if execution_id not in self._callbacks:
            self._callbacks[execution_id] = []
        self._callbacks[execution_id].append(callback)
        logger.debug("Registered progress callback for execution %s", execution_id)

    def unregister_callback(
        self,
        execution_id: str,
        callback: Callable[[WorkflowProgress], Any],
    ) -> None:
        """Unregister a progress callback"""
        if execution_id in self._callbacks:
            try:
                self._callbacks[execution_id].remove(callback)
                logger.debug("Unregistered progress callback for execution %s", execution_id)
            except ValueError:
                logger.debug(
                    "[unregister_callback] Callback not found for execution %s, already removed", execution_id
                )

    def _notify_callbacks(
        self,
        execution_id: str,
        progress: WorkflowProgress,
    ) -> None:
        """Invoke all registered callbacks for an execution"""
        if execution_id in self._callbacks:
            for callback in self._callbacks[execution_id]:
                try:
                    callback(progress)
                except Exception as e:
                    logger.error(
                        "[_notify_callbacks] Error in progress callback for execution_id=%s: %s",
                        execution_id, e,
                        exc_info=True,
                    )

    def cleanup(self, execution_id: str) -> None:
        """Clean up progress tracking for a completed execution"""
        if execution_id in self._progress:
            del self._progress[execution_id]
        if execution_id in self._callbacks:
            del self._callbacks[execution_id]
        logger.debug("Cleaned up progress tracking for execution %s", execution_id)


# Global singleton instance
_workflow_progress_service: WorkflowProgressService | None = None


def get_workflow_progress_service() -> WorkflowProgressService:
    """Get or create the global WorkflowProgressService instance"""
    global _workflow_progress_service
    if _workflow_progress_service is None:
        _workflow_progress_service = WorkflowProgressService()
    return _workflow_progress_service
