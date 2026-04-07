"""
WebSocket Event Broadcaster — stubbed out (no clients connect).

All public functions retained as async no-ops so callers don't break.
"""

from typing import Any, Dict, Optional

from services.logger_config import get_logger

logger = get_logger(__name__)


class WebSocketEventBroadcaster:
    """No-op broadcaster. Every method just logs at DEBUG level."""

    @staticmethod
    async def broadcast_task_progress(task_id: str, **kwargs):
        logger.debug("WS stub: task progress %s", task_id)

    @staticmethod
    async def broadcast_workflow_status(workflow_id: str, **kwargs):
        logger.debug("WS stub: workflow status %s", workflow_id)

    @staticmethod
    async def broadcast_analytics_update(**kwargs):
        pass

    @staticmethod
    async def broadcast_notification(**kwargs):
        pass


# Convenience wrappers — kept for import compatibility
async def emit_task_progress(task_id: str, **kwargs):
    logger.debug("WS stub: emit_task_progress %s", task_id)


async def emit_workflow_status(workflow_id: str, **kwargs):
    pass


async def emit_analytics_update(**kwargs):
    pass


async def emit_notification(**kwargs):
    pass


def emit_task_progress_sync(task_id: str, **kwargs):
    """Sync wrapper — now a pure no-op."""
    logger.debug("WS stub: emit_task_progress_sync %s", task_id)
