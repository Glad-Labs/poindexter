"""Bulk Task Operation Models

Consolidated schemas for bulk task operations.
"""

from pydantic import BaseModel, Field
from typing import Optional, List


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
