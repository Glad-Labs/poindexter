"""Agent Status and Command Models

Consolidated schemas for agent management, monitoring, and control.
"""

from pydantic import BaseModel, Field, field_validator
from typing import Optional, Dict, List, Any
from datetime import datetime
from enum import Enum


class AgentStatusEnum(str, Enum):
    """Agent status values"""

    IDLE = "idle"
    BUSY = "busy"
    ERROR = "error"
    OFFLINE = "offline"


class SystemHealthEnum(str, Enum):
    """System health status values"""

    HEALTHY = "healthy"
    DEGRADED = "degraded"
    ERROR = "error"


class AgentCommandEnum(str, Enum):
    """Available agent commands"""

    EXECUTE = "execute"
    STOP = "stop"
    RESTART = "restart"
    RESET = "reset"
    STATUS = "status"


class AgentStatus(BaseModel):
    """Agent status information"""

    name: str = Field(..., min_length=1, max_length=100, description="Agent name/identifier")
    type: str = Field(..., min_length=1, max_length=100, description="Agent type/role")
    status: AgentStatusEnum = Field(..., description="Current agent status")
    last_activity: Optional[datetime] = Field(None, description="Timestamp of last activity")
    tasks_completed: int = Field(
        default=0, ge=0, description="Number of successfully completed tasks"
    )
    tasks_failed: int = Field(default=0, ge=0, description="Number of failed tasks")
    execution_time_avg: float = Field(
        default=0.0, ge=0.0, description="Average execution time in seconds"
    )
    error_message: Optional[str] = Field(
        None, max_length=1000, description="Last error message if status is error"
    )
    uptime_seconds: int = Field(default=0, ge=0, description="Uptime in seconds")


class AllAgentsStatus(BaseModel):
    """Status of all agents"""

    status: SystemHealthEnum = Field(..., description="Overall system health status")
    timestamp: datetime = Field(..., description="Status timestamp")
    agents: Dict[str, AgentStatus] = Field(..., description="Status of each agent")
    system_health: Dict[str, Any] = Field(..., description="System-level health metrics")


class AgentCommand(BaseModel):
    """Command to send to an agent"""

    command: AgentCommandEnum = Field(..., description="Command to execute")
    parameters: Optional[Dict[str, Any]] = Field(None, description="Command parameters (if any)")

    @field_validator("command")
    @classmethod
    def validate_command(cls, v):
        """Ensure command is valid"""
        if not v:
            raise ValueError("Command cannot be empty")
        return v

    class Config:
        json_schema_extra = {
            "example": {"command": "execute", "parameters": {"task_id": "task_123"}}
        }


class AgentCommandResult(BaseModel):
    """Result of agent command execution"""

    status: str  # "success", "error", "pending"
    message: str
    result: Optional[Dict[str, Any]] = None
    timestamp: datetime


class AgentLog(BaseModel):
    """Agent log entry"""

    timestamp: datetime
    level: str  # "DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"
    agent: str
    message: str
    context: Optional[Dict[str, Any]] = None


class AgentLogs(BaseModel):
    """Collection of agent logs"""

    logs: List[AgentLog]
    total: int
    filtered_by: Dict[str, Any]


class MemoryStats(BaseModel):
    """Memory statistics"""

    total_memories: int
    short_term_count: int
    long_term_count: int
    memory_usage_bytes: int
    memory_usage_mb: float
    last_cleanup: Optional[datetime] = None
    by_agent: Dict[str, Dict[str, Any]]


class AgentHealth(BaseModel):
    """Agent system health status"""

    status: str  # "healthy", "degraded", "error"
    timestamp: datetime
    all_agents_running: bool
    error_count: int
    warning_count: int
    uptime_seconds: int
    details: Dict[str, Any]
