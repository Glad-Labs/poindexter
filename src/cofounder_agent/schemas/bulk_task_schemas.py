"""Bulk Task Operation Models

Consolidated schemas for bulk task operations.
"""

from typing import List, Optional

from pydantic import BaseModel, Field


class BulkTaskRequest(BaseModel):
    """Request schema for bulk task operations"""

    task_ids: List[str]
    action: str  # "pause", "resume", "cancel", "delete"

    class Config:
        json_schema_extra = {
            "example": {
                "task_ids": [
                    "550e8400-e29b-41d4-a716-446655440000",
                    "550e8400-e29b-41d4-a716-446655440001",
                ],
                "action": "cancel",
            }
        }


class BulkTaskResponse(BaseModel):
    """Response schema for bulk operations"""

    message: str
    updated: int
    failed: int
    total: int
    errors: Optional[List[dict]] = None


class BulkCreateTaskItem(BaseModel):
    """Schema for individual task in bulk create request"""

    task_name: str
    topic: str
    primary_keyword: str
    target_audience: str
    category: str
    priority: str = "medium"
    description: Optional[str] = None

    class Config:
        json_schema_extra = {
            "example": {
                "task_name": "Create blog post",
                "topic": "AI in Marketing",
                "primary_keyword": "AI marketing",
                "target_audience": "Marketing managers",
                "category": "Tech",
                "priority": "high",
            }
        }


class BulkCreateTasksRequest(BaseModel):
    """Request schema for creating multiple tasks"""

    tasks: List[BulkCreateTaskItem]

    class Config:
        json_schema_extra = {
            "example": {
                "tasks": [
                    {
                        "task_name": "Task 1",
                        "topic": "Topic 1",
                        "primary_keyword": "keyword1",
                        "target_audience": "Audience 1",
                        "category": "Tech",
                        "priority": "high",
                    }
                ]
            }
        }


class BulkCreateTasksResponse(BaseModel):
    """Response schema for bulk create"""

    created: int
    failed: int
    total: int
    tasks: Optional[List[dict]] = None
    errors: Optional[List[dict]] = None
