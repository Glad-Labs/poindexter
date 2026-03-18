"""
Progress Broadcaster Service

Provides broadcast_progress and broadcast_workflow_progress functions
for use by service-layer code. These were previously in routes/websocket_routes.py,
creating a wrong-direction dependency (services importing from routes).

The functions delegate to the ConnectionManager in websocket_routes via lazy import
to avoid circular imports at module load time.
"""

from services.logger_config import get_logger

logger = get_logger(__name__)


async def broadcast_progress(task_id: str, progress) -> None:
    """Broadcast progress update to all connected clients for a task."""
    if progress is None:
        return

    from routes.websocket_routes import connection_manager

    try:
        await connection_manager.broadcast(
            task_id, {"type": "progress", **progress.to_dict()}
        )
    except Exception as e:
        logger.error(f"[broadcast_progress] Failed to broadcast for task {task_id}: {e}", exc_info=True)


async def broadcast_workflow_progress(execution_id: str, progress) -> None:
    """Broadcast workflow progress update to all connected clients."""
    from routes.websocket_routes import connection_manager

    progress_data = progress if isinstance(progress, dict) else progress.to_dict()
    await connection_manager.broadcast(
        execution_id, {"type": "progress", **progress_data}
    )
