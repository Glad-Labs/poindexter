"""
WebSocket Routes - Real-time progress streaming for image generation

Provides WebSocket endpoints for streaming generation progress to clients
in real-time with live progress bars and status updates.
"""

import logging
import asyncio
import json
from fastapi import APIRouter, WebSocket, WebSocketDisconnect, Depends
from typing import Dict, Set

from services.progress_service import get_progress_service

logger = logging.getLogger(__name__)
websocket_router = APIRouter(prefix="/ws", tags=["WebSocket"])


class ConnectionManager:
    """Manages WebSocket connections and broadcasting"""

    def __init__(self):
        # Store connections by task_id: {task_id -> {connection -> task_id, ...}}
        self.active_connections: Dict[str, Set[WebSocket]] = {}

    async def connect(self, task_id: str, websocket: WebSocket):
        """Register a new WebSocket connection for a task"""
        await websocket.accept()
        if task_id not in self.active_connections:
            self.active_connections[task_id] = set()
        self.active_connections[task_id].add(websocket)
        logger.info(
            f"🔌 WebSocket connected for task {task_id} ({len(self.active_connections[task_id])} total)"
        )

    async def disconnect(self, task_id: str, websocket: WebSocket):
        """Unregister a WebSocket connection"""
        if task_id in self.active_connections:
            self.active_connections[task_id].discard(websocket)
            if not self.active_connections[task_id]:
                del self.active_connections[task_id]
            logger.info(f"🔌 WebSocket disconnected for task {task_id}")

    async def broadcast(self, task_id: str, message: Dict):
        """Broadcast a message to all connected clients for a task"""
        if task_id not in self.active_connections:
            return

        disconnected = set()
        for connection in self.active_connections[task_id]:
            try:
                await connection.send_json(message)
            except Exception as e:
                logger.warning(f"Failed to send message to WebSocket: {e}")
                disconnected.add(connection)

        # Clean up disconnected connections
        for connection in disconnected:
            await self.disconnect(task_id, connection)

    def get_active_connections_count(self, task_id: str) -> int:
        """Get number of active connections for a task"""
        return len(self.active_connections.get(task_id, set()))


# Global connection manager
connection_manager = ConnectionManager()


@websocket_router.websocket("/image-generation/{task_id}")
async def websocket_image_progress(websocket: WebSocket, task_id: str):
    """
    WebSocket endpoint for real-time image generation progress.

    Connect to: ws://localhost:8000/ws/image-generation/{task_id}

    Receives messages like:
    {
        "type": "progress",
        "task_id": "task-123",
        "status": "generating",
        "current_step": 32,
        "total_steps": 50,
        "percentage": 64.0,
        "current_stage": "base_model",
        "elapsed_time": 46.5,
        "estimated_remaining": 26.3,
        "message": "Generating base image (step 32/50)"
    }
    """
    await connection_manager.connect(task_id, websocket)

    try:
        progress_service = get_progress_service()

        # Send initial status
        progress = progress_service.get_progress(task_id)
        if progress:
            await websocket.send_json({"type": "progress", **progress.to_dict()})
        else:
            await websocket.send_json(
                {
                    "type": "status",
                    "message": "Waiting for generation to start...",
                    "task_id": task_id,
                }
            )

        # Keep connection open and receive any client messages
        while True:
            # Wait for client messages (or keep alive)
            try:
                data = await asyncio.wait_for(websocket.receive_text(), timeout=30)
                message = json.loads(data)

                # Handle client commands
                if message.get("type") == "ping":
                    await websocket.send_json({"type": "pong"})
                elif message.get("type") == "get_progress":
                    progress = progress_service.get_progress(task_id)
                    if progress:
                        await websocket.send_json({"type": "progress", **progress.to_dict()})

            except asyncio.TimeoutError:
                # Send keep-alive every 30 seconds
                await websocket.send_json({"type": "keep-alive"})
            except json.JSONDecodeError:
                logger.warning(f"Invalid JSON received on WebSocket: {data}")

    except WebSocketDisconnect:
        await connection_manager.disconnect(task_id, websocket)
        logger.info(f"WebSocket disconnected for task {task_id}")
    except Exception as e:
        logger.error(f"WebSocket error for task {task_id}: {e}")
        await connection_manager.disconnect(task_id, websocket)


async def broadcast_progress(task_id: str, progress) -> None:
    """Broadcast progress update to all connected clients"""
    await connection_manager.broadcast(task_id, {"type": "progress", **progress.to_dict()})


def get_connection_manager() -> ConnectionManager:
    """Get the global connection manager"""
    return connection_manager
