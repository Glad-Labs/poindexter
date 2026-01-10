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

# Orchestrator schemas
from .orchestrator_schemas import (
    ProcessRequestBody,
    ApprovalAction,
    TrainingDataExportRequest,
    TrainingModelUploadRequest,
)

# Quality schemas
from .quality_schemas import (
    QualityEvaluationRequest,
    QualityDimensionsResponse,
    QualityEvaluationResponse,
    BatchQualityRequest,
)

# Task schemas
from .task_schemas import (
    TaskCreateRequest,
    TaskStatusUpdateRequest,
    TaskResponse,
    TaskListResponse,
    MetricsResponse,
    IntentTaskRequest,
    TaskIntentResponse,
    TaskConfirmRequest,
    TaskConfirmResponse,
)

# Content schemas
from .content_schemas import (
    CreateBlogPostRequest,
    TaskStatusResponse,
    BlogDraftResponse,
    DraftsListResponse,
    PublishDraftRequest,
    ApprovalRequest,
    ApprovalResponse,
    PublishDraftResponse,
    GenerateAndPublishRequest,
)

# Unified task response (contains CreateBlogPostResponse as alias)
from .unified_task_response import (
    UnifiedTaskResponse,
    CreateBlogPostResponse,
    ProgressInfo,
)

# Agent schemas
from .agent_schemas import (
    AgentStatus,
    AllAgentsStatus,
    AgentCommand,
    AgentCommandResult,
    AgentLog,
    AgentLogs,
    MemoryStats,
    AgentHealth,
)

# Auth schemas
from .auth_schemas import (
    UserProfile,
    LogoutResponse,
    GitHubCallbackRequest,
)

# Chat schemas
from .chat_schemas import (
    ChatMessage,
    ChatRequest,
    ChatResponse,
)

# Command schemas
from .command_schemas import (
    CommandRequest,
    CommandResponse,
    CommandListResponse,
    CommandResultRequest,
    CommandErrorRequest,
)

# Metrics schemas
from .metrics_schemas import (
    CostMetric,
    CostsResponse,
    HealthMetrics,
    PerformanceMetrics,
)

# Bulk task schemas
from .bulk_task_schemas import (
    BulkTaskRequest,
    BulkTaskResponse,
)

# Natural language schemas
from .natural_language_schemas import (
    NaturalLanguageRequest,
    RefineContentRequest,
    NaturalLanguageResponse,
)

# Ollama schemas
from .ollama_schemas import (
    OllamaHealthResponse,
    OllamaWarmupResponse,
    OllamaModelSelection,
)

# Settings schemas
from .settings_schemas import (
    SettingDataTypeEnum,
    SettingCategoryEnum,
    SettingEnvironmentEnum,
    SettingBase,
    SettingCreate,
    SettingUpdate,
    SettingResponse,
    SettingListResponse,
    SettingHistoryResponse,
    SettingBulkUpdateRequest,
    ErrorResponse,
)

# Database Response Models (Phase 2)
from .database_response_models import (
    UserResponse as DatabaseUserResponse,
    OAuthAccountResponse,
    TaskResponse as DatabaseTaskResponse,
    TaskCountsResponse,
    PostResponse as DatabasePostResponse,
    CategoryResponse,
    TagResponse,
    AuthorResponse,
    LogResponse,
    MetricsResponse as DatabaseMetricsResponse,
    FinancialEntryResponse,
    FinancialSummaryResponse,
    CostLogResponse,
    TaskCostBreakdownResponse,
    QualityEvaluationResponse as DatabaseQualityEvaluationResponse,
    QualityImprovementLogResponse,
    AgentStatusResponse as DatabaseAgentStatusResponse,
    OrchestratorTrainingDataResponse,
    SettingResponse as DatabaseSettingResponse,
    PaginatedResponse,
)

from .model_converter import ModelConverter

# Social schemas
from .social_schemas import (
    SocialPlatformEnum,
    ToneEnum,
    SocialPlatformConnection,
    SocialPost,
    SocialAnalytics,
    GenerateContentRequest,
    CrossPostRequest,
)

# Subtask schemas
from .subtask_schemas import (
    ResearchSubtaskRequest,
    CreativeSubtaskRequest,
    QASubtaskRequest,
    ImageSubtaskRequest,
    FormatSubtaskRequest,
    SubtaskResponse,
)

# Webhook schemas
from .webhooks_schemas import (
    WebhookEntry,
    ContentWebhookPayload,
    WebhookResponse,
)

# Workflow history schemas
from .workflow_history_schemas import (
    WorkflowExecutionDetail,
    WorkflowHistoryResponse,
    WorkflowStatistics,
    PerformanceMetrics,
)

# Models schemas
from .models_schemas import (
    ModelInfo,
    ModelsListResponse,
    ProviderStatus,
    ProvidersStatusResponse,
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
    "UserResponse",
    "OAuthAccountResponse",
    "TaskResponse",
    "TaskCountsResponse",
    "PostResponse",
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
    "AgentStatusResponse",
    "OrchestratorTrainingDataResponse",
    "SettingResponse",
    "ErrorResponse",
    "PaginatedResponse",
    "ModelConverter",
]
