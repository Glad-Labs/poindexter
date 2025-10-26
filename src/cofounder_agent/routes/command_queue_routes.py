"""
Command Queue API Routes

Provides REST endpoints for command dispatch and monitoring
Replaces Pub/Sub topic subscriptions with HTTP API calls
"""

from fastapi import APIRouter, HTTPException, Query
from typing import Dict, Any, Optional, List
from pydantic import BaseModel
import logging
import sys
import os

# Add parent directory to path
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

from services.command_queue import (
    get_command_queue,
    create_command,
    CommandStatus,
)

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/commands", tags=["commands"])


class CommandRequest(BaseModel):
    """Request to create a command"""
    agent_type: str
    action: str
    payload: Optional[Dict[str, Any]] = None


class CommandResponse(BaseModel):
    """Command response"""
    id: str
    agent_type: str
    action: str
    status: str
    result: Optional[Dict[str, Any]] = None
    error: Optional[str] = None
    created_at: str
    updated_at: str
    started_at: Optional[str] = None
    completed_at: Optional[str] = None


class CommandListResponse(BaseModel):
    """List of commands"""
    commands: List[CommandResponse]
    total: int
    status_filter: Optional[str] = None


class CommandResultRequest(BaseModel):
    """Request to mark command as completed"""
    result: Dict[str, Any]


class CommandErrorRequest(BaseModel):
    """Request to mark command as failed"""
    error: str
    retry: bool = True


@router.post("/", response_model=CommandResponse)
async def dispatch_command(request: CommandRequest) -> Dict[str, Any]:
    """
    Dispatch a command to an agent
    
    Replaces Pub/Sub publish pattern.
    Agents poll or receive notifications via webhooks.
    
    Args:
        request: Command request
        
    Returns:
        Created command
    """
    try:
        cmd = await create_command(
            agent_type=request.agent_type,
            action=request.action,
            payload=request.payload or {}
        )
        
        logger.info(f"Command dispatched: {cmd.id} to {request.agent_type}")
        
        return cmd.to_dict()
    except Exception as e:
        logger.error(f"Failed to dispatch command: {e}")
        raise HTTPException(status_code=400, detail=str(e))


@router.get("/{command_id}", response_model=CommandResponse)
async def get_command(command_id: str) -> Dict[str, Any]:
    """
    Get command status and details
    
    Agents use this to poll for command results
    or check completion status.
    
    Args:
        command_id: Command ID
        
    Returns:
        Command details
    """
    queue = get_command_queue()
    cmd = await queue.get_command(command_id)
    
    if not cmd:
        raise HTTPException(status_code=404, detail=f"Command not found: {command_id}")
    
    return cmd.to_dict()


@router.get("/", response_model=CommandListResponse)
async def list_commands(
    status: Optional[str] = Query(None),
    limit: int = Query(100, le=1000),
    skip: int = Query(0, ge=0)
) -> Dict[str, Any]:
    """
    List commands with optional filtering
    
    Args:
        status: Filter by status (pending, processing, completed, failed)
        limit: Number of results
        skip: Number to skip
        
    Returns:
        List of commands
    """
    queue = get_command_queue()
    
    # Parse status filter
    status_filter = None
    if status:
        try:
            status_filter = CommandStatus(status.lower())
        except ValueError:
            raise HTTPException(
                status_code=400,
                detail=f"Invalid status. Must be one of: {', '.join([s.value for s in CommandStatus])}"
            )
    
    # Get commands
    commands = await queue.list_commands(status=status_filter)
    
    # Apply pagination
    total = len(commands)
    commands = commands[skip : skip + limit]
    
    return {
        "commands": [cmd.to_dict() for cmd in commands],
        "total": total,
        "status_filter": status
    }


@router.post("/{command_id}/complete", response_model=CommandResponse)
async def complete_command(
    command_id: str,
    request: CommandResultRequest
) -> Dict[str, Any]:
    """
    Mark command as completed
    
    Agents call this after successful execution.
    
    Args:
        command_id: Command ID
        request: Completion result
        
    Returns:
        Updated command
    """
    queue = get_command_queue()
    cmd = await queue.complete_command(command_id, request.result)
    
    if not cmd:
        raise HTTPException(status_code=404, detail=f"Command not found: {command_id}")
    
    logger.info(f"Command completed: {command_id}")
    
    return cmd.to_dict()


@router.post("/{command_id}/fail", response_model=CommandResponse)
async def fail_command(
    command_id: str,
    request: CommandErrorRequest
) -> Dict[str, Any]:
    """
    Mark command as failed
    
    Agents call this on execution error.
    
    Args:
        command_id: Command ID
        request: Error details and retry flag
        
    Returns:
        Updated command
    """
    queue = get_command_queue()
    cmd = await queue.fail_command(command_id, request.error, retry=request.retry)
    
    if not cmd:
        raise HTTPException(status_code=404, detail=f"Command not found: {command_id}")
    
    logger.info(f"Command failed: {command_id} - {request.error}")
    
    return cmd.to_dict()


@router.post("/{command_id}/cancel", response_model=CommandResponse)
async def cancel_command(command_id: str) -> Dict[str, Any]:
    """
    Cancel a command
    
    Args:
        command_id: Command ID
        
    Returns:
        Updated command
    """
    queue = get_command_queue()
    cmd = await queue.cancel_command(command_id)
    
    if not cmd:
        raise HTTPException(status_code=404, detail=f"Command not found: {command_id}")
    
    logger.info(f"Command cancelled: {command_id}")
    
    return cmd.to_dict()


@router.get("/stats/queue-stats", response_model=Dict[str, Any])
async def get_queue_stats() -> Dict[str, Any]:
    """
    Get command queue statistics
    
    Returns:
        Queue stats (total, pending, by status)
    """
    queue = get_command_queue()
    return queue.get_stats()


@router.post("/cleanup/clear-old")
async def clear_old_commands(max_age_hours: int = Query(24, ge=1, le=720)) -> Dict[str, str]:
    """
    Clear old completed commands
    
    Args:
        max_age_hours: Commands older than this (in hours) will be deleted
        
    Returns:
        Success message
    """
    queue = get_command_queue()
    await queue.clear_old_commands(max_age_hours=max_age_hours)
    
    return {"message": f"Old commands (>{max_age_hours}h) cleared"}
