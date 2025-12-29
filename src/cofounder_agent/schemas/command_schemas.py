"""Command Queue Request/Response Models

Consolidated schemas for command dispatching and tracking.
"""

from pydantic import BaseModel, Field
from typing import Optional, Dict, List, Any


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
