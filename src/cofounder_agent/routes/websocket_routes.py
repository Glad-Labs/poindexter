"""
WebSocket Routes - Real-time progress streaming for image generation

Provides WebSocket endpoints for streaming generation progress to clients
in real-time with live progress bars and status updates.
"""

import asyncio
import json
import logging
from typing import Dict, Set

from fastapi import APIRouter, Depends, WebSocket, WebSocketDisconnect

from services.progress_service import get_progress_service
from services.websocket_manager import websocket_manager

logger = logging.getLogger(__name__)
websocket_router = APIRouter(prefix="/api/ws", tags=["WebSocket"])


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


@websocket_router.websocket("/workflow/{execution_id}")
async def websocket_workflow_progress(websocket: WebSocket, execution_id: str):
    """
    WebSocket endpoint for real-time workflow execution progress.

    Connect to: ws://localhost:8000/api/ws/workflow/{execution_id}

    Receives messages like:
    {
        "type": "progress",
        "execution_id": "550e8400-e29b-41d4-a716-446655440000",
        "status": "executing",
        "current_phase": 1,
        "total_phases": 5,
        "phase_name": "draft",
        "phase_status": "executing",
        "progress_percent": 40.0,
        "completed_phases": 2,
        "elapsed_time": 15.5,
        "estimated_remaining": 23.2,
        "message": "Executing phase 2/5: draft",
        "timestamp": "2026-02-18T10:30:00Z"
    }

    Example Usage (JavaScript):
    ```javascript
    const ws = new WebSocket('ws://localhost:8000/api/ws/workflow/550e8400-e29b-41d4-a716-446655440000');
    ws.addEventListener('message', (event) => {
        const progress = JSON.parse(event.data);
        console.log(`Progress: ${progress.progress_percent}% - ${progress.message}`);
        console.log(`Phase ${progress.current_phase + 1} of ${progress.total_phases}`);
    });
    ```
    """
    await connection_manager.connect(execution_id, websocket)

    try:
        from services.workflow_progress_service import get_workflow_progress_service

        progress_service = get_workflow_progress_service()

        # Send initial status
        progress = progress_service.get_progress(execution_id)
        if progress:
            await websocket.send_json({"type": "progress", **progress.to_dict()})
        else:
            await websocket.send_json(
                {
                    "type": "status",
                    "message": "Waiting for workflow execution to start...",
                    "execution_id": execution_id,
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
                    progress = progress_service.get_progress(execution_id)
                    if progress:
                        await websocket.send_json({"type": "progress", **progress.to_dict()})

            except asyncio.TimeoutError:
                # Send keep-alive every 30 seconds
                await websocket.send_json({"type": "keep-alive"})
            except json.JSONDecodeError:
                logger.warning(f"Invalid JSON received on WebSocket: {data}")

    except WebSocketDisconnect:
        await connection_manager.disconnect(execution_id, websocket)
        logger.info(f"WebSocket disconnected for execution {execution_id}")
    except Exception as e:
        logger.error(f"WebSocket error for execution {execution_id}: {e}")
        await connection_manager.disconnect(execution_id, websocket)


async def broadcast_workflow_progress(execution_id: str, progress) -> None:
    """Broadcast workflow progress update to all connected clients"""
    await connection_manager.broadcast(execution_id, {"type": "progress", **progress.to_dict()})


def get_connection_manager() -> ConnectionManager:
    """Get the global connection manager"""
    return connection_manager


# ============================================================================
# GLOBAL WEBSOCKET ENDPOINT FOR REAL-TIME UPDATES
# ============================================================================


@websocket_router.websocket("/")
async def websocket_endpoint(websocket: WebSocket):
    """
    Global WebSocket endpoint for real-time updates (Phase 4)

    Connect to: ws://localhost:8000/ws

    Clients can subscribe to:
    - Task progress: `task.progress.{task_id}`
    - Workflow status: `workflow.status.{workflow_id}`
    - Analytics updates: `analytics.update`
    - System notifications: `notification.received`

    Message Format:
    {
        "type": "message_type",
        "event": "namespaced.event.name",
        "data": { /* event-specific data */ },
        "timestamp": "2026-02-15T..."
    }

    Example Usage (JavaScript):
    ```javascript
    const ws = new WebSocket('ws://localhost:8000/ws');
    ws.addEventListener('message', (event) => {
        const msg = JSON.parse(event.data);
        if (msg.event === 'task.progress.task-123') {
            console.log('Progress:', msg.data);
        }
    });
    ```
    """
    await websocket.accept()
    namespace = "global"
    
    try:
        # Register connection
        await websocket_manager.connect(websocket, namespace)
        
        logger.info(f"Global WebSocket client connected. Total connections: {websocket_manager.get_connection_count()}")
        
        # Keep connection alive and handle incoming messages
        while True:
            # Receive message from client (for future client->server communication)
            data = await websocket.receive_text()
            logger.debug(f"WebSocket received: {data}")
            
            # Parse the message
            try:
                message = json.loads(data)
                
                # Handle different message types
                if message.get("type") == "subscribe":
                    namespace = message.get("namespace", "global")
                    logger.info(f"Client subscribed to namespace: {namespace}")
                    
                elif message.get("type") == "unsubscribe":
                    namespace = message.get("namespace", "global")
                    logger.info(f"Client unsubscribed from namespace: {namespace}")
                
            except json.JSONDecodeError:
                logger.warning(f"Invalid JSON received: {data}")
    
    except WebSocketDisconnect:
        await websocket_manager.disconnect(websocket, namespace)
        logger.info(f"Global WebSocket client disconnected. Total connections: {websocket_manager.get_connection_count()}")
    
    except Exception as e:
        logger.error(f"WebSocket error: {e}", exc_info=True)
        try:
            await websocket.close(code=1011, reason=str(e))
        except Exception as close_error:
            logger.error(f"Error closing WebSocket: {close_error}")


# Statistics endpoint
@websocket_router.get("/stats")
async def websocket_stats():
    """
    Get WebSocket connection statistics

    Returns:
    {
        "total_connections": 42,
        "namespaces": {
            "global": 10,
            "task.task-123": 5,
            "workflow.workflow-456": 8
        }
    }
    """
    return await websocket_manager.get_stats()
