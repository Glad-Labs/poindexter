"""
Unified Schemas Package

Consolidates all Pydantic models used across the application.

Modules:
- orchestrator_schemas: Orchestration endpoints (process, approve, training)
- quality_schemas: Quality assessment endpoints
- task_schemas: Task management endpoints
- content_schemas: Content creation and management
- auth_schemas: Authentication and user models (when available)
"""

# Agent schemas
from .agent_schemas import (
    AgentCommand,
    AgentCommandResult,
    AgentHealth,
    AgentLog,
    AgentLogs,
    AgentStatus,
    AllAgentsStatus,
    MemoryStats,
)

# Auth schemas
from .auth_schemas import GitHubCallbackRequest, LogoutResponse, UserProfile

# Bulk task schemas
from .bulk_task_schemas import BulkTaskRequest, BulkTaskResponse

# Chat schemas
from .chat_schemas import ChatMessage, ChatRequest, ChatResponse

# Command schemas
from .command_schemas import (
    CommandErrorRequest,
    CommandListResponse,
    CommandRequest,
    CommandResponse,
    CommandResultRequest,
)

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

# Natural language schemas
from .natural_language_schemas import (
    NaturalLanguageRequest,
    NaturalLanguageResponse,
    RefineContentRequest,
)

# Ollama schemas
from .ollama_schemas import OllamaHealthResponse, OllamaModelSelection, OllamaWarmupResponse

# Orchestrator schemas
from .orchestrator_schemas import (
    ApprovalAction,
    ProcessRequestBody,
    TrainingDataExportRequest,
    TrainingModelUploadRequest,
)

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

# Social schemas
from .social_schemas import (
    CrossPostRequest,
    GenerateContentRequest,
    SocialAnalytics,
    SocialPlatformConnection,
    SocialPlatformEnum,
    SocialPost,
    ToneEnum,
)

# Subtask schemas
from .subtask_schemas import (
    CreativeSubtaskRequest,
    FormatSubtaskRequest,
    ImageSubtaskRequest,
    QASubtaskRequest,
    ResearchSubtaskRequest,
    SubtaskResponse,
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
    # Orchestrator
    "ProcessRequestBody",
    "ApprovalAction",
    "TrainingDataExportRequest",
    "TrainingModelUploadRequest",
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
    # Agent
    "AgentStatus",
    "AllAgentsStatus",
    "AgentCommand",
    "AgentCommandResult",
    "AgentLog",
    "AgentLogs",
    "MemoryStats",
    "AgentHealth",
    # Auth
    "UserProfile",
    "LogoutResponse",
    "GitHubCallbackRequest",
    # Chat
    "ChatMessage",
    "ChatRequest",
    "ChatResponse",
    # Command
    "CommandRequest",
    "CommandResponse",
    "CommandListResponse",
    "CommandResultRequest",
    "CommandErrorRequest",
    # Metrics
    "CostMetric",
    "CostsResponse",
    "HealthMetrics",
    "PerformanceMetrics",
    # Bulk task
    "BulkTaskRequest",
    "BulkTaskResponse",
    # Natural language
    "NaturalLanguageRequest",
    "RefineContentRequest",
    "NaturalLanguageResponse",
    # Ollama
    "OllamaHealthResponse",
    "OllamaWarmupResponse",
    "OllamaModelSelection",
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
    # Social
    "SocialPlatformEnum",
    "ToneEnum",
    "SocialPlatformConnection",
    "SocialPost",
    "SocialAnalytics",
    "GenerateContentRequest",
    "CrossPostRequest",
    # Subtask
    "ResearchSubtaskRequest",
    "CreativeSubtaskRequest",
    "QASubtaskRequest",
    "ImageSubtaskRequest",
    "FormatSubtaskRequest",
    "SubtaskResponse",
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
