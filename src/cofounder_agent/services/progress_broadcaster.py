"""
Progress Broadcaster — stubbed out (no WebSocket clients connect).

Retains the two public async functions as no-ops.
"""

from services.logger_config import get_logger

logger = get_logger(__name__)


async def broadcast_progress(task_id: str, progress) -> None:
    """No-op: no WebSocket clients to receive progress."""
    if progress is None:
        return
    logger.debug("WS stub: broadcast_progress %s", task_id)


async def broadcast_workflow_progress(execution_id: str, _progress) -> None:
    """No-op: no WebSocket clients to receive workflow progress."""
    logger.debug("WS stub: broadcast_workflow_progress %s", execution_id)
