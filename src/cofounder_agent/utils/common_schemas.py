"""
Common Schemas - Consolidated Pydantic models across routes

This module consolidates duplicate schema definitions from multiple route files
(content, task, subtask, settings, etc.) into a single source of truth.

Provides:
- Pagination request/response models
- Common request/response base models
- Shared validation patterns
- Reusable field definitions with consistent validation

Eliminates:
- Duplicate schema definitions
- Inconsistent field validation
- Multiple definitions of same concept
- Schema maintenance across files
"""

from datetime import datetime
from typing import Any, Generic, List, Optional, TypeVar

from pydantic import BaseModel, ConfigDict, Field

# ============================================================================
# PAGINATION MODELS
# ============================================================================


class PaginationParams(BaseModel):
    """Standard pagination parameters for list endpoints"""

    skip: int = Field(0, ge=0, description="Number of items to skip")
    limit: int = Field(10, ge=1, le=100, description="Maximum items to return")

    class Config:
        json_schema_extra = {"example": {"skip": 0, "limit": 20}}


class PaginationMeta(BaseModel):
    """Pagination metadata for list responses"""

    total: int = Field(..., description="Total number of items")
    skip: int = Field(..., description="Items skipped")
    limit: int = Field(..., description="Items returned")
    has_more: bool = Field(..., description="Whether more items exist")

    class Config:
        json_schema_extra = {"example": {"total": 150, "skip": 0, "limit": 20, "has_more": True}}


T = TypeVar("T")


class PaginatedResponse(BaseModel, Generic[T]):
    """Generic paginated response container"""

    status: str = Field("success", description="Response status")
    data: List[T] = Field(..., description="List of items")
    pagination: PaginationMeta = Field(..., description="Pagination metadata")
    request_id: Optional[str] = Field(None, description="Request ID for tracing")
    timestamp: Optional[str] = Field(None, description="ISO 8601 timestamp")


# ============================================================================
# BASE REQUEST/RESPONSE MODELS
# ============================================================================


class BaseRequest(BaseModel):
    """Base class for all request models with common configuration"""

    model_config = ConfigDict(str_strip_whitespace=True, validate_default=True)


class BaseResponse(BaseModel):
    """Base class for all response models"""

    id: str = Field(..., description="Unique identifier")
    created_at: Optional[datetime] = Field(None, description="Creation timestamp")
    updated_at: Optional[datetime] = Field(None, description="Last update timestamp")

    model_config = ConfigDict(from_attributes=True)


# ============================================================================
# TASK-RELATED SCHEMAS
# ============================================================================


class TaskBaseRequest(BaseRequest):
    """Base request model for task creation/update"""

    task_name: str = Field(..., min_length=1, max_length=255, description="Task name")
    description: Optional[str] = Field(None, max_length=2000, description="Task description")
    priority: Optional[str] = Field(
        "medium", pattern="^(low|medium|high|critical)$", description="Task priority level"
    )
    due_date: Optional[datetime] = Field(None, description="Task due date")
    assignee: Optional[str] = Field(None, description="Assigned user ID")
    tags: Optional[List[str]] = Field(None, description="Task tags")


class TaskCreateRequest(TaskBaseRequest):
    """Request model for task creation"""

    project_id: Optional[str] = Field(None, description="Associated project ID")


class TaskUpdateRequest(BaseRequest):
    """Request model for task update (all fields optional)"""

    task_name: Optional[str] = Field(None, min_length=1, max_length=255)
    description: Optional[str] = Field(None, max_length=2000)
    priority: Optional[str] = Field(None, pattern="^(low|medium|high|critical)$")
    due_date: Optional[datetime] = Field(None)
    assignee: Optional[str] = Field(None)
    tags: Optional[List[str]] = Field(None)
    status: Optional[str] = Field(None)


class TaskResponse(BaseResponse):
    """Response model for task"""

    task_name: str = Field(..., description="Task name")
    description: Optional[str] = Field(None, description="Task description")
    priority: str = Field(..., description="Task priority")
    status: str = Field("pending", description="Task status")
    due_date: Optional[datetime] = Field(None, description="Task due date")
    assignee: Optional[str] = Field(None, description="Assigned user ID")
    tags: Optional[List[str]] = Field(None, description="Task tags")
    project_id: Optional[str] = Field(None, description="Associated project ID")

    model_config = ConfigDict(from_attributes=True)


class TaskListResponse(BaseResponse):
    """Lightweight response model for task lists"""

    task_name: str = Field(..., description="Task name")
    status: str = Field("pending", description="Task status")
    priority: str = Field(..., description="Task priority")
    due_date: Optional[datetime] = Field(None, description="Task due date")


# ============================================================================
# SUBTASK-RELATED SCHEMAS
# ============================================================================


class SubtaskBaseRequest(BaseRequest):
    """Base request model for subtask creation/update"""

    subtask_name: str = Field(..., min_length=1, max_length=255, description="Subtask name")
    description: Optional[str] = Field(None, max_length=2000, description="Subtask description")
    estimated_hours: Optional[float] = Field(None, ge=0, description="Estimated hours")
    assigned_to: Optional[str] = Field(None, description="Assigned user ID")
    dependencies: Optional[List[str]] = Field(None, description="Dependent subtask IDs")


class SubtaskCreateRequest(SubtaskBaseRequest):
    """Request model for subtask creation"""

    task_id: str = Field(..., description="Parent task ID")


class SubtaskUpdateRequest(BaseRequest):
    """Request model for subtask update"""

    subtask_name: Optional[str] = Field(None, min_length=1, max_length=255)
    description: Optional[str] = Field(None, max_length=2000)
    estimated_hours: Optional[float] = Field(None, ge=0)
    assigned_to: Optional[str] = Field(None)
    dependencies: Optional[List[str]] = Field(None)
    status: Optional[str] = Field(None)


class SubtaskResponse(BaseResponse):
    """Response model for subtask"""

    subtask_name: str = Field(..., description="Subtask name")
    description: Optional[str] = Field(None, description="Subtask description")
    task_id: str = Field(..., description="Parent task ID")
    status: str = Field("pending", description="Subtask status")
    estimated_hours: Optional[float] = Field(None, description="Estimated hours")
    assigned_to: Optional[str] = Field(None, description="Assigned user ID")
    dependencies: Optional[List[str]] = Field(None, description="Dependent subtask IDs")

    model_config = ConfigDict(from_attributes=True)


# ============================================================================
# CONTENT-RELATED SCHEMAS
# ============================================================================


class ContentBaseRequest(BaseRequest):
    """Base request model for content creation/update"""

    title: str = Field(..., min_length=1, max_length=500, description="Content title")
    body: Optional[str] = Field(
        None, max_length=50000, description="Content body (up to 50,000 characters)"
    )
    content_type: Optional[str] = Field("text", description="Content type (text, html, markdown)")
    topic: Optional[str] = Field(None, max_length=255, description="Content topic")


class ContentCreateRequest(ContentBaseRequest):
    """Request model for content creation"""

    topic: str = Field(..., min_length=1, max_length=255, description="Content topic")


class ContentUpdateRequest(BaseRequest):
    """Request model for content update"""

    title: Optional[str] = Field(None, min_length=1, max_length=500)
    body: Optional[str] = Field(None, max_length=50000)
    content_type: Optional[str] = Field(None)
    topic: Optional[str] = Field(None, max_length=255)
    is_published: Optional[bool] = Field(None)


class ContentResponse(BaseResponse):
    """Response model for content"""

    title: str = Field(..., description="Content title")
    body: Optional[str] = Field(None, description="Content body")
    content_type: str = Field(..., description="Content type")
    topic: str = Field(..., description="Content topic")
    is_published: bool = Field(False, description="Publication status")
    author_id: Optional[str] = Field(None, description="Author user ID")

    model_config = ConfigDict(from_attributes=True)


# ============================================================================
# SETTINGS-RELATED SCHEMAS
# ============================================================================


class SettingsBaseRequest(BaseRequest):
    """Base request model for settings"""

    key: str = Field(..., min_length=1, max_length=255, description="Setting key")
    value: Optional[Any] = Field(None, description="Setting value")
    description: Optional[str] = Field(None, max_length=500, description="Setting description")


class SettingsUpdateRequest(BaseRequest):
    """Request model for settings update"""

    value: Optional[Any] = Field(None, description="Setting value")
    description: Optional[str] = Field(None, max_length=500)


class SettingsResponse(BaseResponse):
    """Response model for settings"""

    key: str = Field(..., description="Setting key")
    value: Optional[Any] = Field(None, description="Setting value")
    description: Optional[str] = Field(None, description="Setting description")
    value_type: Optional[str] = Field(None, description="Setting value type")

    model_config = ConfigDict(from_attributes=True)


# ============================================================================
# ID VALIDATION SCHEMAS
# ============================================================================


class IdPathParam(BaseModel):
    """Path parameter for resource ID"""

    id: str = Field(..., description="Resource ID")


class IdsQuery(BaseModel):
    """Query parameter for multiple IDs"""

    ids: List[str] = Field(..., description="List of resource IDs")


# ============================================================================
# BULK OPERATION SCHEMAS
# ============================================================================


class BulkCreateRequest(BaseRequest):
    """Base class for bulk creation requests"""

    items: List[dict] = Field(..., min_items=1, max_items=100, description="Items to create")


class BulkUpdateRequest(BaseRequest):
    """Base class for bulk update requests"""

    updates: List[dict] = Field(..., min_items=1, max_items=100, description="Items to update")


class BulkDeleteRequest(BaseRequest):
    """Base class for bulk delete requests"""

    ids: List[str] = Field(..., min_items=1, max_items=100, description="IDs to delete")


class BulkOperationResponse(BaseModel):
    """Response for bulk operations"""

    status: str = Field("success", description="Operation status")
    total_processed: int = Field(..., description="Total items processed")
    successful: int = Field(..., description="Successful operations")
    failed: int = Field(..., description="Failed operations")
    errors: Optional[List[dict]] = Field(None, description="Error details")
    timestamp: Optional[str] = Field(None, description="ISO 8601 timestamp")


# ============================================================================
# FILTER & SEARCH SCHEMAS
# ============================================================================


class SearchParams(BaseRequest):
    """Common search parameters"""

    query: Optional[str] = Field(None, max_length=500, description="Search query")
    sort_by: Optional[str] = Field(None, description="Field to sort by")
    sort_order: Optional[str] = Field("asc", pattern="^(asc|desc)$", description="Sort order")


class FilterParams(BaseRequest):
    """Common filter parameters"""

    status: Optional[str] = Field(None, description="Filter by status")
    created_after: Optional[datetime] = Field(None, description="Filter by creation date")
    created_before: Optional[datetime] = Field(None, description="Filter by creation date")
    updated_after: Optional[datetime] = Field(None, description="Filter by update date")
    updated_before: Optional[datetime] = Field(None, description="Filter by update date")


# ============================================================================
# USAGE SUMMARY
# ============================================================================

"""
BEFORE (scattered schema definitions):
======================================

In content_routes.py:
    class ContentCreateRequest(BaseModel):
        title: str
        body: Optional[str]
        topic: str

In task_routes.py:
    class TaskCreateRequest(BaseModel):
        task_name: str
        description: Optional[str]
        priority: Optional[str] = "medium"

In subtask_routes.py:
    class TaskCreateRequest(BaseModel):  # Duplicate name!
        subtask_name: str
        description: Optional[str]

PROBLEMS:
  ❌ Duplicate definitions across files
  ❌ Inconsistent validation rules
  ❌ Same name in different files
  ❌ Hard to maintain consistency
  ❌ No shared base models


AFTER (consolidated schemas):
==============================

In any route file:
    from utils.common_schemas import (
        ContentCreateRequest,
        TaskCreateRequest,
        TaskUpdateRequest,
        TaskResponse,
        SubtaskCreateRequest,
        PaginationParams,
        PaginatedResponse
    )
    
    @app.post("/content")
    async def create_content(request: ContentCreateRequest):
        ...
    
    @app.get("/tasks")
    async def list_tasks(params: PaginationParams):
        tasks = await db.list_tasks(skip=params.skip, limit=params.limit)
        return PaginatedResponse(
            data=[TaskResponse.from_orm(t) for t in tasks],
            pagination=PaginationMeta(
                total=count,
                skip=params.skip,
                limit=params.limit,
                has_more=count > params.skip + params.limit
            )
        )

BENEFITS:
  ✅ Single source of truth
  ✅ Consistent validation
  ✅ Reusable pagination models
  ✅ Generic PaginatedResponse
  ✅ Base classes for common patterns
  ✅ No name conflicts
  ✅ Easy to maintain
  ✅ Type-safe
  ✅ Well-documented fields
  ✅ Consistent error messages
"""
