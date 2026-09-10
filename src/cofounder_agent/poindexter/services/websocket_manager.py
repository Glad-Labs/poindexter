"""
WebSocket Manager — stubbed out (no clients connect).

Retains the public API surface so existing imports don't break,
but every method is a no-op.  The singleton ``websocket_manager``
is still importable for metrics_routes and other code that reads
``active_connections``.
"""

import json
from dataclasses import asdict, dataclass
from datetime import datetime, timezone

from services.logger_config import get_logger

logger = get_logger(__name__)


@dataclass
class WebSocketMessage:
    """Standard WebSocket message format (kept for import compatibility)."""

    type: str
    event: str
    data: dict
    timestamp: str | None = None

    def __post_init__(self):
        if self.timestamp is None:
            self.timestamp = datetime.now(timezone.utc).isoformat()

    def to_json(self) -> str:
        return json.dumps(asdict(self))


class WebSocketManager:
    """No-op WebSocket manager. No clients connect; all methods are cheap stubs."""

    def __init__(self):
        self.active_connections: dict = {}
        self.connection_count = 0

    # -- kept for forward-compat if WS is ever re-enabled --

    async def connect(self, websocket, namespace: str = "global"):
        pass

    async def disconnect(self, websocket, namespace: str = "global"):
        pass

    async def broadcast_to_namespace(self, namespace, message_type, event, data):
        pass

    async def broadcast_to_all(self, message_type, event, data):
        pass

    async def send_task_progress(self, task_id: str, progress_data: dict) -> None:
        logger.debug("WS stub: task progress %s (no clients)", task_id)

    async def send_workflow_status(self, workflow_id: str, status_data: dict) -> None:
        logger.debug("WS stub: workflow status %s (no clients)", workflow_id)

    async def send_analytics_update(self, analytics_data: dict):
        pass

    async def send_notification(self, notification_data: dict):
        pass

    def get_connection_count(self) -> int:
        return 0

    def get_namespace_count(self, namespace: str) -> int:
        return 0

    async def get_stats(self) -> dict:
        return {"total_connections": 0, "namespaces": {}}


# Global instance
websocket_manager = WebSocketManager()
