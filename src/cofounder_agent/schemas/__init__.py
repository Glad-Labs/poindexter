"""
Unified Schemas Package

Consolidates all Pydantic models used across the application.

Modules:
- task_schemas: Task management endpoints
- settings_schemas: Application settings
- database_response_models: Typed DB response models
"""

# Database Response Models (Phase 2)
from .database_response_models import (
    AuthorResponse,
    CategoryResponse,
    CostLogResponse,
    ErrorResponse,
    FinancialEntryResponse,
    FinancialSummaryResponse,
    LogResponse,
    OAuthAccountResponse,
    OrchestratorTrainingDataResponse,
    PaginatedResponse,
    QualityImprovementLogResponse,
    TagResponse,
    TaskCostBreakdownResponse,
    TaskCountsResponse,
)
from .model_converter import ModelConverter

# Settings schemas
from .settings_schemas import (
    SettingBase,
    SettingBulkUpdateRequest,
    SettingCategoryEnum,
    SettingCreate,
    SettingDataTypeEnum,
    SettingEnvironmentEnum,
    SettingHistoryResponse,
    SettingListResponse,
    SettingResponse,
    SettingUpdate,
)

# Task schemas
from .task_schemas import (
    IntentTaskRequest,
    MetricsResponse,
    TaskConfirmRequest,
    TaskConfirmResponse,
    TaskCreateRequest,
    TaskIntentResponse,
    TaskListResponse,
    TaskResponse,
)
from .task_status_schemas import TaskStatusUpdateRequest

# Typed dict shapes for SQL helper returns (#201 — lightweight peer of
# the Pydantic models above; gives mypy/pyright field knowledge over
# dict returns without per-call validation cost)
from .typed_records import (
    CostBreakdownRecord,
    PaginatedTasksResult,
    PostRecord,
    TaskCountsRecord,
    TaskRecord,
    UserRecord,
)

# Unified task response (contains CreateBlogPostResponse as alias)
from .unified_task_response import CreateBlogPostResponse, ProgressInfo, UnifiedTaskResponse

__all__ = [
    # Task
    "TaskCreateRequest",
    "TaskStatusUpdateRequest",
    "TaskResponse",
    "TaskListResponse",
    "MetricsResponse",
    "IntentTaskRequest",
    "TaskIntentResponse",
    "TaskConfirmRequest",
    "TaskConfirmResponse",
    # Content
    "CreateBlogPostResponse",
    "UnifiedTaskResponse",
    "ProgressInfo",
    # Settings
    "SettingDataTypeEnum",
    "SettingCategoryEnum",
    "SettingEnvironmentEnum",
    "SettingBase",
    "SettingCreate",
    "SettingUpdate",
    "SettingResponse",
    "SettingListResponse",
    "SettingHistoryResponse",
    "SettingBulkUpdateRequest",
    "ErrorResponse",
    # Database Response Models (Phase 2)
    "OAuthAccountResponse",
    "TaskResponse",
    "TaskCountsResponse",
    "CategoryResponse",
    "TagResponse",
    "AuthorResponse",
    "LogResponse",
    "MetricsResponse",
    "FinancialEntryResponse",
    "FinancialSummaryResponse",
    "CostLogResponse",
    "TaskCostBreakdownResponse",
    "QualityImprovementLogResponse",
    "OrchestratorTrainingDataResponse",
    "SettingResponse",
    "ErrorResponse",
    "PaginatedResponse",
    "ModelConverter",
    # Typed-Record layer (#201)
    "TaskRecord",
    "PostRecord",
    "UserRecord",
    "CostBreakdownRecord",
    "TaskCountsRecord",
    "PaginatedTasksResult",
]
