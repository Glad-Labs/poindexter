"""
WebSocket Routes - Real-time progress streaming for image generation

Provides WebSocket endpoints for streaming generation progress to clients
in real-time with live progress bars and status updates.
"""

import asyncio
import json
import logging
import os
from typing import Dict, Optional, Set

from fastapi import APIRouter, Depends, Query, WebSocket, WebSocketDisconnect

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
            except (ValueError, KeyError, AttributeError, TypeError, RuntimeError) as e:
                logger.warning(f"Failed to send message to WebSocket: {e}", exc_info=True)
                disconnected.add(connection)

        # Clean up disconnected connections
        for connection in disconnected:
            await self.disconnect(task_id, connection)

    def get_active_connections_count(self, task_id: str) -> int:
        """Get number of active connections for a task"""
        return len(self.active_connections.get(task_id, set()))


# Global connection manager
connection_manager = ConnectionManager()


async def _validate_ws_token(websocket: WebSocket, token: str) -> bool:
    """
    Validate a WebSocket token query parameter.

    Returns True if valid (or dev bypass accepted), False and closes the
    connection with code 1008 if invalid.
    """
    if os.getenv("DEVELOPMENT_MODE", "false").lower() == "true" and token.lower().startswith("dev-"):
        return True
    try:
        from services.token_validator import JWTTokenValidator

        claims = JWTTokenValidator.verify_token(token)
        if not claims:
            await websocket.close(code=1008, reason="Invalid token")
            return False
    except Exception:
        await websocket.close(code=1008, reason="Invalid token")
        return False
    return True


@websocket_router.websocket("/image-generation/{task_id}")
async def websocket_image_progress(websocket: WebSocket, task_id: str, token: str = Query(...)):
    """
    WebSocket endpoint for real-time image generation progress.

    Connect to: ws://localhost:8000/ws/image-generation/{task_id}?token=<jwt>

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
    if not await _validate_ws_token(websocket, token):
        return
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
                logger.warning(f"Invalid JSON received on WebSocket: {data}")  # type: ignore[possibly-undefined]

    except WebSocketDisconnect:
        await connection_manager.disconnect(task_id, websocket)
        logger.info(f"WebSocket disconnected for task {task_id}")
    except (ValueError, KeyError, AttributeError, TypeError, RuntimeError) as e:
        logger.error(f"WebSocket error for task {task_id}: {e}", exc_info=True)
        await connection_manager.disconnect(task_id, websocket)


async def broadcast_progress(task_id: str, progress) -> None:
    """Broadcast progress update to all connected clients"""
    await connection_manager.broadcast(task_id, {"type": "progress", **progress.to_dict()})


async def broadcast_approval_status(
    task_id: str, status: str, details: Optional[Dict] = None
) -> None:
    """Broadcast approval status change to all connected clients"""
    message = {
        "type": "approval_status",
        "task_id": task_id,
        "status": status,  # approved, rejected, pending_revision
        "timestamp": asyncio.get_event_loop().time(),
    }
    if details:
        message.update(details)
    await connection_manager.broadcast(task_id, message)


@websocket_router.websocket("/workflow/{execution_id}")
async def websocket_workflow_progress(websocket: WebSocket, execution_id: str, token: str = Query(...)):
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
    if not await _validate_ws_token(websocket, token):
        return
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
                logger.warning(f"Invalid JSON received on WebSocket: {data}")  # type: ignore[possibly-undefined]

    except WebSocketDisconnect:
        await connection_manager.disconnect(execution_id, websocket)
        logger.info(f"WebSocket disconnected for execution {execution_id}")
    except (ValueError, KeyError, AttributeError, TypeError, RuntimeError) as e:
        logger.error(f"WebSocket error for execution {execution_id}: {e}", exc_info=True)
        await connection_manager.disconnect(execution_id, websocket)


async def broadcast_workflow_progress(execution_id: str, progress) -> None:
    """Broadcast workflow progress update to all connected clients"""
    # If progress is a dict, use it directly; if it has to_dict(), call it
    progress_data = progress if isinstance(progress, dict) else progress.to_dict()
    await connection_manager.broadcast(execution_id, {"type": "progress", **progress_data})


def get_connection_manager() -> ConnectionManager:
    """Get the global connection manager"""
    return connection_manager


# ============================================================================
# GLOBAL WEBSOCKET ENDPOINT FOR REAL-TIME UPDATES
# ============================================================================


@websocket_router.websocket("/")
async def websocket_endpoint(websocket: WebSocket, token: str = Query(...)):
    """
    Global WebSocket endpoint for real-time updates (Phase 4)

    Connect to: ws://localhost:8000/ws?token=<jwt>

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
    const ws = new WebSocket('ws://localhost:8000/ws?token=<jwt>');
    ws.addEventListener('message', (event) => {
        const msg = JSON.parse(event.data);
        if (msg.event === 'task.progress.task-123') {
            console.log('Progress:', msg.data);
        }
    });
    ```
    """
    if not await _validate_ws_token(websocket, token):
        return
    await websocket.accept()
    active_namespaces = {"global"}

    try:
        # Register connection in the global namespace
        await websocket_manager.connect(websocket, "global")

        logger.info(
            f"Global WebSocket client connected. Total connections: {websocket_manager.get_connection_count()}"
        )

        # Keep connection alive and handle incoming messages
        while True:
            # Receive message from client (or send keep-alive on timeout)
            try:
                data = await asyncio.wait_for(websocket.receive_text(), timeout=30)
            except asyncio.TimeoutError:
                await websocket.send_json({"type": "keep-alive"})
                continue
            logger.debug(f"WebSocket received: {data}")

            # Parse the message
            try:
                message = json.loads(data)

                # Handle different message types
                if message.get("type") == "subscribe":
                    new_ns = message.get("namespace", "global")
                    if new_ns not in active_namespaces:
                        await websocket_manager.connect(websocket, new_ns)
                        active_namespaces.add(new_ns)
                    logger.info(f"Client subscribed to namespace: {new_ns}")

                elif message.get("type") == "unsubscribe":
                    ns_to_remove = message.get("namespace", "global")
                    if ns_to_remove in active_namespaces and ns_to_remove != "global":
                        await websocket_manager.disconnect(websocket, ns_to_remove)
                        active_namespaces.discard(ns_to_remove)
                    logger.info(f"Client unsubscribed from namespace: {ns_to_remove}")

            except json.JSONDecodeError:
                logger.warning(f"Invalid JSON received: {data}")

    except WebSocketDisconnect:
        for ns in list(active_namespaces):
            await websocket_manager.disconnect(websocket, ns)
        logger.info(
            f"Global WebSocket client disconnected. Total connections: {websocket_manager.get_connection_count()}"
        )

    except (ValueError, KeyError, AttributeError, TypeError, RuntimeError) as e:
        logger.error(f"WebSocket error: {e}", exc_info=True)
        for ns in list(active_namespaces):
            try:
                await websocket_manager.disconnect(websocket, ns)
            except (ValueError, KeyError, AttributeError, TypeError, RuntimeError) as disconnect_error:
                logger.debug(
                    f"[websocket_cleanup] Error disconnecting from namespace {ns}: {disconnect_error}"
                )
        try:
            await websocket.close(code=1011, reason=str(e))
        except (ValueError, KeyError, AttributeError, TypeError, RuntimeError) as close_error:
            logger.error(f"Error closing WebSocket: {close_error}", exc_info=True)


# Statistics endpoint


@websocket_router.websocket("/approval/{task_id}")
async def websocket_approval_updates(websocket: WebSocket, task_id: str, token: str = Query(...)):
    """
    WebSocket endpoint for real-time approval status updates.

    Connect to: ws://localhost:8000/api/ws/approval/{task_id}?token=<jwt>

    Receives messages like:
    {
        "type": "approval_status",
        "task_id": "task-123",
        "status": "approved|rejected|pending_revision",
        "feedback": "Additional feedback from reviewer",
        "timestamp": 1645234567.89
    }

    Usage in ApprovalQueue component:
    - Connect when component mounts
    - Listen for status changes
    - Update UI when approval/rejection happens
    - Disconnect when component unmounts
    """
    if not await _validate_ws_token(websocket, token):
        return
    await connection_manager.connect(task_id, websocket)

    try:
        while True:
            try:
                # Keep connection alive and wait for updates
                # Timeout after 60 seconds and send keep-alive
                data = await asyncio.wait_for(websocket.receive_text(), timeout=60)
                logger.debug(f"Approval WebSocket received from {task_id}: {data}")

                # Handle any client messages if needed in future
                try:
                    message = json.loads(data)
                    # Could handle client requests here (e.g., refresh status)
                except json.JSONDecodeError:
                    logger.warning(f"Invalid JSON on approval WebSocket for {task_id}: {data}")

            except asyncio.TimeoutError:
                # Send keep-alive every 60 seconds
                await websocket.send_json({"type": "keep-alive", "task_id": task_id})

    except WebSocketDisconnect:
        await connection_manager.disconnect(task_id, websocket)
        logger.info(f"Approval WebSocket disconnected for task {task_id}")
    except (ValueError, KeyError, AttributeError, TypeError, RuntimeError) as e:
        logger.error(f"Approval WebSocket error for task {task_id}: {e}", exc_info=True)
        await connection_manager.disconnect(task_id, websocket)


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
