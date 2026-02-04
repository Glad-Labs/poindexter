"""
Progress Service - Real-time progress tracking for long-running tasks

Provides callbacks and storage for tracking generation progress
to be streamed to WebSocket clients in real-time.
"""

import logging
from dataclasses import asdict, dataclass
from datetime import datetime
from typing import Any, Callable, Dict, Optional

logger = logging.getLogger(__name__)


@dataclass
class GenerationProgress:
    """Represents the current state of a generation task"""

    task_id: str
    status: str  # pending, generating, completed, failed
    current_step: int = 0
    total_steps: int = 0
    percentage: float = 0.0
    current_stage: str = ""  # "base_model", "refiner_model", "encoding", etc.
    elapsed_time: float = 0.0
    estimated_remaining: float = 0.0
    error: Optional[str] = None
    message: str = ""
    timestamp: str = ""

    def __post_init__(self):
        if not self.timestamp:
            self.timestamp = datetime.now().isoformat()

    def to_dict(self) -> Dict[str, Any]:
        """Convert to dictionary for JSON serialization"""
        return asdict(self)


class ProgressService:
    """Manages progress tracking for generation tasks"""

    def __init__(self):
        # Store progress by task_id: {task_id -> GenerationProgress}
        self._progress: Dict[str, GenerationProgress] = {}
        # Store callbacks by task_id: {task_id -> [callback1, callback2, ...]}
        self._callbacks: Dict[str, list[Callable]] = {}

    def create_progress(self, task_id: str, total_steps: int = 50) -> GenerationProgress:
        """Initialize progress tracking for a new task"""
        progress = GenerationProgress(
            task_id=task_id,
            status="pending",
            total_steps=total_steps,
            message="Initializing generation...",
        )
        self._progress[task_id] = progress
        logger.info(f"📊 Created progress tracker for {task_id}")
        return progress

    def get_progress(self, task_id: str) -> Optional[GenerationProgress]:
        """Get current progress for a task"""
        return self._progress.get(task_id)

    def update_progress(
        self,
        task_id: str,
        current_step: int,
        total_steps: Optional[int] = None,
        stage: Optional[str] = None,
        elapsed_time: Optional[float] = None,
        message: Optional[str] = None,
    ) -> GenerationProgress:
        """Update progress for a generation step"""
        progress = self._progress.get(task_id)
        if not progress:
            # Create if doesn't exist
            progress = self.create_progress(task_id, total_steps or 50)

        if total_steps is not None:
            progress.total_steps = total_steps

        progress.current_step = current_step
        progress.status = "generating"

        # Calculate percentage
        if progress.total_steps > 0:
            progress.percentage = (current_step / progress.total_steps) * 100

        if stage:
            progress.current_stage = stage

        if elapsed_time is not None:
            progress.elapsed_time = elapsed_time
            # Estimate remaining time
            if current_step > 0 and progress.total_steps > 0:
                time_per_step = elapsed_time / current_step
                remaining_steps = progress.total_steps - current_step
                progress.estimated_remaining = time_per_step * remaining_steps

        if message:
            progress.message = message

        progress.timestamp = datetime.now().isoformat()

        # Call registered callbacks
        self._notify_callbacks(task_id, progress)

        return progress

    def mark_complete(
        self, task_id: str, message: str = "Generation complete"
    ) -> GenerationProgress:
        """Mark a task as completed"""
        progress = self._progress.get(task_id)
        if progress:
            progress.status = "completed"
            progress.current_step = progress.total_steps
            progress.percentage = 100.0
            progress.message = message
            progress.estimated_remaining = 0.0
            progress.timestamp = datetime.now().isoformat()

            # Call registered callbacks
            self._notify_callbacks(task_id, progress)

        return progress

    def mark_failed(self, task_id: str, error: str) -> GenerationProgress:
        """Mark a task as failed"""
        progress = self._progress.get(task_id)
        if progress:
            progress.status = "failed"
            progress.error = error
            progress.message = f"Generation failed: {error}"
            progress.timestamp = datetime.now().isoformat()

            # Call registered callbacks
            self._notify_callbacks(task_id, progress)

        return progress

    def register_callback(
        self, task_id: str, callback: Callable[[GenerationProgress], Any]
    ) -> None:
        """Register a callback to be called when progress is updated"""
        if task_id not in self._callbacks:
            self._callbacks[task_id] = []
        self._callbacks[task_id].append(callback)

    def _notify_callbacks(self, task_id: str, progress: GenerationProgress) -> None:
        """Notify all registered callbacks for a task"""
        callbacks = self._callbacks.get(task_id, [])
        for callback in callbacks:
            try:
                callback(progress)
            except Exception as e:
                logger.error(f"Error in progress callback: {e}")

    def cleanup(self, task_id: str) -> None:
        """Clean up progress and callbacks for a task"""
        self._progress.pop(task_id, None)
        self._callbacks.pop(task_id, None)


# Global progress service instance
_progress_service: Optional[ProgressService] = None


def get_progress_service() -> ProgressService:
    """Get or create the global progress service"""
    global _progress_service
    if _progress_service is None:
        _progress_service = ProgressService()
    return _progress_service
