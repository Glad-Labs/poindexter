"""
Progress Broadcaster Service

Provides broadcast_progress and broadcast_workflow_progress functions
for use by service-layer code. Delegates to the service-layer
WebSocketManager to avoid importing from the routes layer.
"""

from services.logger_config import get_logger
from services.websocket_manager import websocket_manager

logger = get_logger(__name__)


async def broadcast_progress(task_id: str, progress) -> None:
    """Broadcast progress update to all connected clients for a task."""
    if progress is None:
        return

    try:
        progress_data = progress if isinstance(progress, dict) else progress.to_dict()
        await websocket_manager.send_task_progress(task_id, progress_data)
    except Exception as e:
        logger.error(
            f"[broadcast_progress] Failed to broadcast for task {task_id}: {e}", exc_info=True
        )


async def broadcast_workflow_progress(execution_id: str, progress) -> None:
    """Broadcast workflow progress update to all connected clients."""
    try:
        progress_data = progress if isinstance(progress, dict) else progress.to_dict()
        await websocket_manager.send_workflow_status(execution_id, progress_data)
    except Exception as e:
        logger.error(
            f"[broadcast_workflow_progress] Failed to broadcast for execution {execution_id}: {e}",
            exc_info=True,
        )
