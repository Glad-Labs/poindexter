"""
Unified Schemas Package

Consolidates all Pydantic models used across the application.

Modules:
- quality_schemas: Quality assessment endpoints
- task_schemas: Task management endpoints
- content_schemas: Content creation and management
- auth_schemas: Authentication and user models
- settings_schemas: Application settings
- metrics_schemas: Cost and performance metrics
- bulk_task_schemas: Bulk task operations
- database_response_models: Typed DB response models
"""

# Auth schemas
from .auth_schemas import GitHubCallbackRequest, LogoutResponse, UserProfile

# Bulk task schemas
from .bulk_task_schemas import BulkTaskRequest, BulkTaskResponse

# Content schemas
from .content_schemas import (
    ApprovalRequest,
    ApprovalResponse,
    BlogDraftResponse,
    CreateBlogPostRequest,
    DraftsListResponse,
    GenerateAndPublishRequest,
    PublishDraftRequest,
    PublishDraftResponse,
    TaskStatusResponse,
)

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

# Metrics schemas
from .metrics_schemas import CostMetric, CostsResponse, HealthMetrics, PerformanceMetrics
from .model_converter import ModelConverter

# Models schemas
from .models_schemas import ModelInfo, ModelsListResponse, ProvidersStatusResponse, ProviderStatus

# Quality schemas
from .quality_schemas import (
    BatchQualityRequest,
    QualityDimensionsResponse,
    QualityEvaluationRequest,
    QualityEvaluationResponse,
)

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

# Unified task response (contains CreateBlogPostResponse as alias)
from .unified_task_response import CreateBlogPostResponse, ProgressInfo, UnifiedTaskResponse

# Webhook schemas
from .webhooks_schemas import ContentWebhookPayload, WebhookEntry, WebhookResponse

# Workflow history schemas
from .workflow_history_schemas import (
    WorkflowExecutionDetail,
    WorkflowHistoryResponse,
    WorkflowStatistics,
)

__all__ = [
    # Quality
    "QualityEvaluationRequest",
    "QualityDimensionsResponse",
    "QualityEvaluationResponse",
    "BatchQualityRequest",
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
    "CreateBlogPostRequest",
    "CreateBlogPostResponse",
    "UnifiedTaskResponse",
    "ProgressInfo",
    "TaskStatusResponse",
    "BlogDraftResponse",
    "DraftsListResponse",
    "PublishDraftRequest",
    "ApprovalRequest",
    "ApprovalResponse",
    "PublishDraftResponse",
    "GenerateAndPublishRequest",
    # Auth
    "UserProfile",
    "LogoutResponse",
    "GitHubCallbackRequest",
    # Metrics
    "CostMetric",
    "CostsResponse",
    "HealthMetrics",
    "PerformanceMetrics",
    # Bulk task
    "BulkTaskRequest",
    "BulkTaskResponse",
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
    # Webhook
    "WebhookEntry",
    "ContentWebhookPayload",
    "WebhookResponse",
    # Workflow history
    "WorkflowExecutionDetail",
    "WorkflowHistoryResponse",
    "WorkflowStatistics",
    "PerformanceMetrics",
    # Models
    "ModelInfo",
    "ModelsListResponse",
    "ProviderStatus",
    "ProvidersStatusResponse",
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
    "QualityEvaluationResponse",
    "QualityImprovementLogResponse",
    "OrchestratorTrainingDataResponse",
    "SettingResponse",
    "ErrorResponse",
    "PaginatedResponse",
    "ModelConverter",
]
