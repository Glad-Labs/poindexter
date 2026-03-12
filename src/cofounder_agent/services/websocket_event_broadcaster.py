"""
WebSocket Event Broadcaster
Provides convenient functions for other services to emit WebSocket events
"""

import asyncio
from services.logger_config import get_logger
from typing import Any, Dict, Optional

from services.websocket_manager import websocket_manager

logger = get_logger(__name__)
class WebSocketEventBroadcaster:
    """
    Broadcasts WebSocket events from various services
    Can be called from task executors, workflows, analytics, etc.
    """

    @staticmethod
    async def broadcast_task_progress(
        task_id: str,
        status: str,
        progress: int,
        current_step: str,
        total_steps: int,
        completed_steps: int,
        message: str,
        elapsed_time: Optional[float] = None,
        estimated_time_remaining: Optional[float] = None,
        error: Optional[str] = None,
    ):
        """
        Broadcast task progress update

        Args:
            task_id: Task ID
            status: Task status (PENDING, RUNNING, COMPLETED, FAILED, PAUSED)
            progress: Progress percentage (0-100)
            current_step: Current operation description
            total_steps: Total number of steps
            completed_steps: Number of completed steps
            message: Status message
            elapsed_time: Elapsed time in seconds
            estimated_time_remaining: Estimated remaining time in seconds
            error: Error message (if failed)
        """
        await websocket_manager.send_task_progress(
            task_id=task_id,
            progress_data={
                "status": status,
                "progress": progress,
                "currentStep": current_step,
                "totalSteps": total_steps,
                "completedSteps": completed_steps,
                "message": message,
                "elapsedTime": elapsed_time,
                "estimatedTimeRemaining": estimated_time_remaining,
                "error": error,
            },
        )
        logger.debug(f"Broadcast task progress for {task_id}: {status} {progress}%")

    @staticmethod
    async def broadcast_workflow_status(
        workflow_id: str,
        status: str,
        duration: Optional[float] = None,
        task_count: Optional[int] = None,
        task_results: Optional[Dict[str, Any]] = None,
    ):
        """
        Broadcast workflow status update

        Args:
            workflow_id: Workflow ID
            status: Workflow status (PENDING, RUNNING, COMPLETED, FAILED, PAUSED)
            duration: Workflow duration in seconds
            task_count: Number of tasks in workflow
            task_results: Results from individual tasks
        """
        await websocket_manager.send_workflow_status(
            workflow_id=workflow_id,
            status_data={
                "status": status,
                "duration": duration,
                "taskCount": task_count,
                "taskResults": task_results or {},
            },
        )
        logger.debug(f"Broadcast workflow status for {workflow_id}: {status}")

    @staticmethod
    async def broadcast_analytics_update(
        total_tasks: Optional[int] = None,
        completed_today: Optional[int] = None,
        average_completion_time: Optional[float] = None,
        cost_today: Optional[float] = None,
        success_rate: Optional[float] = None,
        failed_today: Optional[int] = None,
        running_now: Optional[int] = None,
    ):
        """
        Broadcast analytics update to all clients

        Args:
            total_tasks: Total number of tasks ever
            completed_today: Tasks completed today
            average_completion_time: Average completion time in seconds
            cost_today: Total cost today
            success_rate: Success rate percentage (0-100)
            failed_today: Tasks failed today
            running_now: Tasks currently running
        """
        analytics_data = {}

        if total_tasks is not None:
            analytics_data["totalTasks"] = total_tasks
        if completed_today is not None:
            analytics_data["completedToday"] = completed_today
        if average_completion_time is not None:
            analytics_data["averageCompletionTime"] = average_completion_time
        if cost_today is not None:
            analytics_data["costToday"] = cost_today
        if success_rate is not None:
            analytics_data["successRate"] = success_rate
        if failed_today is not None:
            analytics_data["failedToday"] = failed_today
        if running_now is not None:
            analytics_data["runningNow"] = running_now

        await websocket_manager.send_analytics_update(analytics_data)
        logger.debug(f"Broadcast analytics update with keys: {list(analytics_data.keys())}")

    @staticmethod
    async def broadcast_notification(
        type: str,
        title: str,
        message: str,
        duration: int = 5000,
    ):
        """
        Broadcast notification to all clients

        Args:
            type: Notification type (success, error, warning, info)
            title: Notification title
            message: Notification message
            duration: Display duration in milliseconds
        """
        await websocket_manager.send_notification(
            {
                "type": type,
                "title": title,
                "message": message,
                "duration": duration,
            }
        )
        logger.debug(f"Broadcast notification: {type} - {title}")


# Convenience function for quick access
async def emit_task_progress(task_id: str, **kwargs):
    """Emit task progress update"""
    await WebSocketEventBroadcaster.broadcast_task_progress(task_id=task_id, **kwargs)


async def emit_workflow_status(workflow_id: str, **kwargs):
    """Emit workflow status update"""
    await WebSocketEventBroadcaster.broadcast_workflow_status(workflow_id=workflow_id, **kwargs)


async def emit_analytics_update(**kwargs):
    """Emit analytics update"""
    await WebSocketEventBroadcaster.broadcast_analytics_update(**kwargs)


async def emit_notification(**kwargs):
    """Emit notification"""
    await WebSocketEventBroadcaster.broadcast_notification(**kwargs)


# For non-async contexts, provide a wrapper
def emit_task_progress_sync(task_id: str, **kwargs):
    """Emit task progress update from non-async context"""
    try:
        loop = asyncio.get_running_loop()
        # Running loop exists -- schedule as a task (most common in FastAPI)
        asyncio.create_task(emit_task_progress(task_id=task_id, **kwargs))
    except RuntimeError:
        # No running loop -- try to create one and run synchronously
        try:
            asyncio.run(emit_task_progress(task_id=task_id, **kwargs))
        except RuntimeError:
            logger.error(
                "[emit_task_progress_sync] Could not emit task progress: no event loop",
                exc_info=True,
            )
