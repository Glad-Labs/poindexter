"""
Task Management Schemas

Consolidates all Pydantic models for task management endpoints
"""

from typing import Optional, Dict, Any, List
from pydantic import BaseModel, Field


class TaskCreateRequest(BaseModel):
    """Schema for creating a new task"""
    task_name: str = Field(..., min_length=3, max_length=200, 
                          description="Name of the task (3-200 chars)")
    topic: str = Field(..., min_length=3, max_length=200, 
                      description="Blog post topic (3-200 chars)")
    primary_keyword: str = Field(default="", max_length=100, 
                                description="Primary SEO keyword (max 100 chars)")
    target_audience: str = Field(default="", max_length=100, 
                                description="Target audience (max 100 chars)")
    category: str = Field(default="general", max_length=50, 
                         description="Content category (max 50 chars)")
    metadata: Optional[Dict[str, Any]] = Field(default_factory=dict, 
                                              description="Additional metadata")
    
    class Config:
        json_schema_extra = {
            "example": {
                "task_name": "Blog Post - AI in Healthcare",
                "topic": "How AI is Transforming Healthcare",
                "primary_keyword": "AI healthcare",
                "target_audience": "Healthcare professionals",
                "category": "healthcare",
                "metadata": {"priority": "high"}
            }
        }


class TaskStatusUpdateRequest(BaseModel):
    """Schema for updating task status"""
    status: str = Field(..., pattern="^(pending|in_progress|completed|failed|cancelled)$",
                       description="New task status")
    result: Optional[Dict[str, Any]] = Field(None, description="Task result/output")
    metadata: Optional[Dict[str, Any]] = Field(None, description="Additional metadata")
    
    class Config:
        json_schema_extra = {
            "example": {
                "status": "completed",
                "result": {"content": "Generated blog post..."},
                "metadata": {"execution_time": 45.2}
            }
        }


class TaskResponse(BaseModel):
    """Schema for task response"""
    id: Optional[str] = None
    task_name: str
    agent_id: Optional[str] = None
    status: str
    topic: str
    primary_keyword: Optional[str]
    target_audience: Optional[str]
    category: Optional[str]
    created_at: str
    updated_at: str
    started_at: Optional[str] = None
    completed_at: Optional[str] = None
    metadata: Dict[str, Any] = {}
    task_metadata: Dict[str, Any] = {}
    result: Optional[Dict[str, Any]] = None
    error_message: Optional[str] = None
    error_details: Optional[Dict[str, Any]] = None
    
    @property
    def title(self) -> str:
        """Alias for task_name for frontend compatibility"""
        return self.task_name
    
    @property
    def name(self) -> str:
        """Alias for task_name for frontend compatibility"""
        return self.task_name
    
    class Config:
        json_schema_extra = {
            "example": {
                "id": "550e8400-e29b-41d4-a716-446655440000",
                "task_name": "Blog Post - AI in Healthcare",
                "agent_id": "content-agent",
                "status": "completed",
                "topic": "How AI is Transforming Healthcare",
                "primary_keyword": "AI healthcare",
                "target_audience": "Healthcare professionals",
                "category": "healthcare",
                "created_at": "2024-01-15T10:30:00Z",
                "updated_at": "2024-01-15T10:45:00Z",
                "started_at": "2024-01-15T10:32:00Z",
                "completed_at": "2024-01-15T10:45:00Z",
            }
        }


class TaskListResponse(BaseModel):
    """Schema for task list response with pagination"""
    tasks: List[TaskResponse]
    total: int
    offset: int
    limit: int
