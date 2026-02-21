"""
Workflow Event Emitter - Integrates workflow execution with progress tracking and WebSocket broadcasting

Provides callbacks for workflow execution phases that automatically track progress
and broadcast updates to connected WebSocket clients.
"""

import asyncio
import logging
from typing import Callable, Dict, Optional

logger = logging.getLogger(__name__)


class WorkflowEventEmitter:
    """Emits workflow execution events for progress tracking and broadcasting"""

    def __init__(self):
        self.handlers: Dict[str, Callable] = {}
        self._progress_service = None
        self._broadcast_function = None

    def set_progress_service(self, progress_service):
        """Set the progress service for tracking"""
        self._progress_service = progress_service

    def set_broadcast_function(self, broadcast_func):
        """Set the broadcast function for WebSocket updates"""
        self._broadcast_function = broadcast_func

    def register_handler(self, event_type: str, handler: Callable) -> None:
        """Register a handler for a specific event type"""
        self.handlers[event_type] = handler
        logger.debug(f"Registered handler for event type: {event_type}")

    async def emit_execution_started(
        self,
        execution_id: str,
        total_phases: int,
    ) -> None:
        """Emit when workflow execution starts"""
        if self._progress_service:
            progress = self._progress_service.start_execution(
                execution_id,
                message=f"Starting workflow execution with {total_phases} phases...",
            )
            await self._broadcast_progress(execution_id, progress)

    async def emit_phase_started(
        self,
        execution_id: str,
        phase_index: int,
        phase_name: str,
    ) -> None:
        """Emit when a phase starts executing"""
        if self._progress_service:
            progress = self._progress_service.start_phase(
                execution_id,
                phase_index,
                phase_name,
            )
            await self._broadcast_progress(execution_id, progress)
            logger.info(f"[{execution_id}] Phase started: {phase_name}")

    async def emit_phase_completed(
        self,
        execution_id: str,
        phase_name: str,
        phase_output: Optional[Dict] = None,
        duration_ms: Optional[float] = None,
    ) -> None:
        """Emit when a phase completes successfully"""
        if self._progress_service:
            progress = self._progress_service.complete_phase(
                execution_id,
                phase_name,
                phase_output,
                duration_ms,
            )
            await self._broadcast_progress(execution_id, progress)
            logger.info(
                f"[{execution_id}] Phase completed: {phase_name} " f"(duration: {duration_ms}ms)"
            )

    async def emit_phase_failed(
        self,
        execution_id: str,
        phase_name: str,
        error: str,
    ) -> None:
        """Emit when a phase fails"""
        if self._progress_service:
            progress = self._progress_service.fail_phase(
                execution_id,
                phase_name,
                error,
            )
            await self._broadcast_progress(execution_id, progress)
            logger.warning(f"[{execution_id}] Phase failed: {phase_name} - {error}")

    async def emit_execution_completed(
        self,
        execution_id: str,
        final_output: Optional[Dict] = None,
        duration_ms: Optional[float] = None,
    ) -> None:
        """Emit when workflow execution completes successfully"""
        if self._progress_service:
            progress = self._progress_service.mark_complete(
                execution_id,
                final_output,
                duration_ms,
                message="Workflow execution completed successfully",
            )
            await self._broadcast_progress(execution_id, progress)
            logger.info(f"[{execution_id}] Workflow completed (duration: {duration_ms}ms)")

    async def emit_execution_failed(
        self,
        execution_id: str,
        error: str,
        failed_phase: Optional[str] = None,
    ) -> None:
        """Emit when workflow execution fails"""
        if self._progress_service:
            progress = self._progress_service.mark_failed(
                execution_id,
                error,
                failed_phase,
            )
            await self._broadcast_progress(execution_id, progress)
            logger.error(
                f"[{execution_id}] Workflow failed " f"(phase: {failed_phase}, error: {error})"
            )

    async def _broadcast_progress(self, execution_id: str, progress) -> None:
        """Broadcast progress to WebSocket clients"""
        if self._broadcast_function:
            try:
                # Call broadcast in a non-blocking way if it's async
                if asyncio.iscoroutinefunction(self._broadcast_function):
                    await self._broadcast_function(execution_id, progress)
                else:
                    self._broadcast_function(execution_id, progress)
            except Exception as e:
                logger.error(
                    f"Error broadcasting progress for {execution_id}: {e}",
                    exc_info=True,
                )


# Global singleton instance
_workflow_event_emitter: Optional[WorkflowEventEmitter] = None


def get_workflow_event_emitter() -> WorkflowEventEmitter:
    """Get or create the global WorkflowEventEmitter instance"""
    global _workflow_event_emitter
    if _workflow_event_emitter is None:
        _workflow_event_emitter = WorkflowEventEmitter()
    return _workflow_event_emitter
