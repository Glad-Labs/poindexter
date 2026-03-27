"""
WebSocket Manager for Real-time Updates
Handles WebSocket connections, message broadcasting, and event streaming
"""

import asyncio
import json
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from typing import Dict, Optional, Set

from services.logger_config import get_logger

logger = get_logger(__name__)


@dataclass
class WebSocketMessage:
    """Standard WebSocket message format"""

    type: str
    event: str
    data: dict
    timestamp: Optional[str] = None

    def __post_init__(self):
        if self.timestamp is None:
            self.timestamp = datetime.now(timezone.utc).isoformat()

    def to_json(self) -> str:
        """Convert message to JSON string"""
        return json.dumps(asdict(self))


class WebSocketManager:
    """
    Manages WebSocket connections and broadcasts events to connected clients
    """

    def __init__(self):
        """Initialize WebSocket manager"""
        self.active_connections: Dict[str, Set] = {}  # {namespace: {connections}}
        self.connection_count = 0
        self.lock = asyncio.Lock()

    async def connect(self, websocket, namespace: str = "global"):
        """Register a new WebSocket connection"""
        async with self.lock:
            if namespace not in self.active_connections:
                self.active_connections[namespace] = set()

            self.active_connections[namespace].add(websocket)
            self.connection_count += 1

            logger.info(
                f"WebSocket connected to namespace '{namespace}' "
                f"(total: {self.connection_count})"
            )

    async def disconnect(self, websocket, namespace: str = "global"):
        """Unregister a WebSocket connection"""
        async with self.lock:
            if namespace in self.active_connections:
                self.active_connections[namespace].discard(websocket)
                self.connection_count -= 1

                logger.info(
                    f"WebSocket disconnected from namespace '{namespace}' "
                    f"(total: {self.connection_count})"
                )

    async def broadcast_to_namespace(
        self,
        namespace: str,
        message_type: str,
        event: str,
        data: dict,
    ):
        """Broadcast message to all connections in a namespace"""
        message = WebSocketMessage(
            type=message_type,
            event=event,
            data=data,
        )

        message_json = message.to_json()
        disconnected = set()

        async with self.lock:
            connections = self.active_connections.get(namespace, set()).copy()

        for connection in connections:
            try:
                await connection.send_text(message_json)
            except Exception as e:
                logger.error(
                    f"[_broadcast_to_namespace] Failed to send message to connection: {e}",
                    exc_info=True,
                )
                disconnected.add(connection)

        # Clean up disconnected connections
        if disconnected:
            async with self.lock:
                for conn in disconnected:
                    self.active_connections[namespace].discard(conn)
                    self.connection_count -= 1

    async def broadcast_to_all(
        self,
        message_type: str,
        event: str,
        data: dict,
    ):
        """Broadcast message to all namespaces"""
        for namespace in self.active_connections:
            await self.broadcast_to_namespace(namespace, message_type, event, data)

    async def send_task_progress(self, task_id: str, progress_data: dict) -> None:
        """Send task progress update"""
        await self.broadcast_to_namespace(
            namespace=f"task.{task_id}",
            message_type="progress",
            event=f"task.progress.{task_id}",
            data={
                "taskId": task_id,
                **progress_data,
            },
        )

    async def send_workflow_status(self, workflow_id: str, status_data: dict) -> None:
        """Send workflow status update"""
        await self.broadcast_to_namespace(
            namespace=f"workflow.{workflow_id}",
            message_type="workflow_status",
            event=f"workflow.status.{workflow_id}",
            data={
                "workflowId": workflow_id,
                **status_data,
            },
        )

    async def send_analytics_update(self, analytics_data: dict):
        """Broadcast analytics update to all connected clients"""
        await self.broadcast_to_all(
            message_type="analytics",
            event="analytics.update",
            data=analytics_data,
        )

    async def send_notification(self, notification_data: dict):
        """Broadcast notification to all connected clients"""
        await self.broadcast_to_all(
            message_type="notification",
            event="notification.received",
            data=notification_data,
        )

    def get_connection_count(self) -> int:
        """Get total number of active connections"""
        return self.connection_count

    def get_namespace_count(self, namespace: str) -> int:
        """Get number of connections in a specific namespace"""
        return len(self.active_connections.get(namespace, set()))

    async def get_stats(self) -> dict:
        """Get WebSocket connection statistics"""
        async with self.lock:
            return {
                "total_connections": self.connection_count,
                "namespaces": {
                    ns: len(conns)
                    for ns, conns in self.active_connections.items()
                    if len(conns) > 0
                },
            }


# Global instance
websocket_manager = WebSocketManager()
