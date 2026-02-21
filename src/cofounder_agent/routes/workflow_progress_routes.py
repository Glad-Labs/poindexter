"""
Workflow Progress Tracking Routes

Provides REST endpoints and WebSocket integration for:
- Tracking workflow execution progress in real-time
- Broadcasting progress updates via WebSocket
- Querying historical progress data
- Setting progress alerts/thresholds
"""

import json
import logging
from datetime import datetime
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query, WebSocket
from services.workflow_progress_service import (
    WorkflowProgressService,
    get_workflow_progress_service,
)

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/workflow-progress", tags=["workflow-progress"])

# Global WebSocket connections for broadcasting
active_connections: dict[str, list[WebSocket]] = {}


@router.post("/initialize/{execution_id}")
async def initialize_progress(
    execution_id: str,
    workflow_id: Optional[str] = None,
    template: Optional[str] = None,
    total_phases: int = 0,
    progress_service: WorkflowProgressService = Depends(get_workflow_progress_service),
) -> dict:
    """
    Initialize progress tracking for a workflow execution.

    Args:
        execution_id: Unique execution identifier
        workflow_id: Optional workflow ID
        template: Optional template name
        total_phases: Total number of phases in workflow
        progress_service: Injected progress service

    Returns:
        Created progress tracking object
    """
    try:
        progress = progress_service.create_progress(
            execution_id=execution_id,
            workflow_id=workflow_id,
            template=template,
            total_phases=total_phases,
        )
        logger.info(f"Initialized progress for execution {execution_id}")
        return {"success": True, "execution_id": execution_id, "progress": progress.to_dict()}
    except Exception as e:
        logger.error(f"Failed to initialize progress: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/start/{execution_id}")
async def start_execution(
    execution_id: str,
    message: str = "Starting workflow execution...",
    progress_service: WorkflowProgressService = Depends(get_workflow_progress_service),
) -> dict:
    """
    Mark a workflow execution as started.

    Args:
        execution_id: Execution identifier
        message: Optional status message
        progress_service: Injected progress service

    Returns:
        Updated progress object
    """
    try:
        progress = progress_service.start_execution(execution_id, message=message)
        await broadcast_workflow_progress(execution_id, progress.to_dict())
        logger.info(f"Started execution {execution_id}")
        return {"success": True, "progress": progress.to_dict()}
    except Exception as e:
        logger.error(f"Failed to start execution: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/phase/start/{execution_id}")
async def start_phase(
    execution_id: str,
    phase_index: int,
    phase_name: str,
    message: Optional[str] = None,
    progress_service: WorkflowProgressService = Depends(get_workflow_progress_service),
) -> dict:
    """
    Mark the start of a workflow phase.

    Args:
        execution_id: Execution identifier
        phase_index: Index of the phase (0-based)
        phase_name: Name of the phase
        message: Optional status message
        progress_service: Injected progress service

    Returns:
        Updated progress object
    """
    try:
        progress = progress_service.start_phase(
            execution_id=execution_id,
            phase_index=phase_index,
            phase_name=phase_name,
            message=message,
        )
        await broadcast_workflow_progress(execution_id, progress.to_dict())
        logger.debug(f"Started phase {phase_index} for execution {execution_id}")
        return {"success": True, "progress": progress.to_dict()}
    except Exception as e:
        logger.error(f"Failed to start phase: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/phase/complete/{execution_id}")
async def complete_phase(
    execution_id: str,
    phase_name: str,
    phase_output: Optional[dict] = None,
    duration_ms: Optional[float] = None,
    progress_service: WorkflowProgressService = Depends(get_workflow_progress_service),
) -> dict:
    """
    Mark a workflow phase as completed.

    Args:
        execution_id: Execution identifier
        phase_name: Name of the completed phase
        phase_output: Optional phase output data
        duration_ms: Time taken for phase (ms)
        progress_service: Injected progress service

    Returns:
        Updated progress object
    """
    try:
        progress = progress_service.complete_phase(
            execution_id=execution_id,
            phase_name=phase_name,
            phase_output=phase_output,
            duration_ms=duration_ms,
        )
        await broadcast_workflow_progress(execution_id, progress.to_dict())
        logger.debug(f"Completed phase {phase_name} for execution {execution_id}")
        return {"success": True, "progress": progress.to_dict()}
    except Exception as e:
        logger.error(f"Failed to complete phase: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/phase/fail/{execution_id}")
async def fail_phase(
    execution_id: str,
    phase_name: str,
    error: str,
    progress_service: WorkflowProgressService = Depends(get_workflow_progress_service),
) -> dict:
    """
    Mark a workflow phase as failed.

    Args:
        execution_id: Execution identifier
        phase_name: Name of the failed phase
        error: Error message
        progress_service: Injected progress service

    Returns:
        Updated progress object
    """
    try:
        progress = progress_service.fail_phase(
            execution_id=execution_id,
            phase_name=phase_name,
            error=error,
        )
        await broadcast_workflow_progress(execution_id, progress.to_dict())
        logger.warning(f"Phase {phase_name} failed for execution {execution_id}: {error}")
        return {"success": True, "progress": progress.to_dict()}
    except Exception as e:
        logger.error(f"Failed to mark phase as failed: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/complete/{execution_id}")
async def mark_complete(
    execution_id: str,
    final_output: Optional[dict] = None,
    duration_ms: Optional[float] = None,
    message: str = "Workflow execution completed",
    progress_service: WorkflowProgressService = Depends(get_workflow_progress_service),
) -> dict:
    """
    Mark a workflow execution as completed.

    Args:
        execution_id: Execution identifier
        final_output: Optional final output data
        duration_ms: Total execution time (ms)
        message: Completion message
        progress_service: Injected progress service

    Returns:
        Final progress object
    """
    try:
        progress = progress_service.mark_complete(
            execution_id=execution_id,
            final_output=final_output,
            duration_ms=duration_ms,
            message=message,
        )
        await broadcast_workflow_progress(execution_id, progress.to_dict())
        logger.info(f"Completed execution {execution_id}")
        return {"success": True, "progress": progress.to_dict()}
    except Exception as e:
        logger.error(f"Failed to mark execution as complete: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/fail/{execution_id}")
async def mark_failed(
    execution_id: str,
    error: str,
    failed_phase: Optional[str] = None,
    progress_service: WorkflowProgressService = Depends(get_workflow_progress_service),
) -> dict:
    """
    Mark a workflow execution as failed.

    Args:
        execution_id: Execution identifier
        error: Error message
        failed_phase: Optional name of phase that failed
        progress_service: Injected progress service

    Returns:
        Final progress object
    """
    try:
        progress = progress_service.mark_failed(
            execution_id=execution_id,
            error=error,
            failed_phase=failed_phase,
        )
        await broadcast_workflow_progress(execution_id, progress.to_dict())
        logger.error(f"Execution {execution_id} failed: {error}")
        return {"success": True, "progress": progress.to_dict()}
    except Exception as e:
        logger.error(f"Failed to mark execution as failed: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/status/{execution_id}")
async def get_progress_status(
    execution_id: str,
    progress_service: WorkflowProgressService = Depends(get_workflow_progress_service),
) -> dict:
    """
    Get current progress status for a workflow execution.

    Args:
        execution_id: Execution identifier
        progress_service: Injected progress service

    Returns:
        Current progress object with phase details
    """
    try:
        progress = progress_service.get_progress(execution_id)
        if not progress:
            raise HTTPException(status_code=404, detail=f"No progress found for {execution_id}")
        return progress.to_dict()
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Failed to get progress status: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.websocket("/ws/{execution_id}")
async def websocket_workflow_progress(websocket: WebSocket, execution_id: str):
    """
    WebSocket endpoint for real-time workflow progress updates.

    Clients connect with execution_id and receive progress updates as they occur.

    Args:
        websocket: WebSocket connection
        execution_id: Execution identifier to track
    """
    await websocket.accept()
    logger.info(f"WebSocket connected for execution {execution_id}")

    # Register connection
    if execution_id not in active_connections:
        active_connections[execution_id] = []
    active_connections[execution_id].append(websocket)

    try:
        while True:
            # Keep connection alive and receive any messages
            data = await websocket.receive_text()
            if data == "ping":
                await websocket.send_text("pong")
    except Exception as e:
        logger.debug(f"WebSocket error for {execution_id}: {e}")
    finally:
        # Unregister connection
        if execution_id in active_connections:
            active_connections[execution_id].remove(websocket)
            if not active_connections[execution_id]:
                del active_connections[execution_id]
        logger.info(f"WebSocket disconnected for execution {execution_id}")


async def broadcast_workflow_progress(execution_id: str, progress: dict) -> None:
    """
    Broadcast progress update to all connected WebSocket clients.

    Args:
        execution_id: Execution identifier
        progress: Progress object to broadcast
    """
    if execution_id not in active_connections:
        return

    message = json.dumps(
        {
            "type": "progress_update",
            "execution_id": execution_id,
            "timestamp": datetime.utcnow().isoformat(),
            "progress": progress,
        }
    )

    # Send to all connected clients
    disconnected = []
    for websocket in active_connections[execution_id]:
        try:
            await websocket.send_text(message)
        except Exception as e:
            logger.debug(f"Failed to send progress to client: {e}")
            disconnected.append(websocket)

    # Clean up disconnected clients
    for websocket in disconnected:
        active_connections[execution_id].remove(websocket)


@router.delete("/cleanup/{execution_id}")
async def cleanup_progress(
    execution_id: str,
    progress_service: WorkflowProgressService = Depends(get_workflow_progress_service),
) -> dict:
    """
    Clean up progress tracking for a completed workflow execution.

    Args:
        execution_id: Execution identifier
        progress_service: Injected progress service

    Returns:
        Confirmation of cleaned up progress
    """
    try:
        progress_service.cleanup(execution_id)
        logger.info(f"Cleaned up progress for execution {execution_id}")
        return {"success": True, "message": f"Progress cleaned up for {execution_id}"}
    except Exception as e:
        logger.error(f"Failed to clean up progress: {e}")
        raise HTTPException(status_code=500, detail=str(e))
