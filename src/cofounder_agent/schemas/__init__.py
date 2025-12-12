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
)

# Task schemas
from .task_schemas import (
    TaskCreateRequest,
    TaskStatusUpdateRequest,
    TaskResponse,
    TaskListResponse,
)

# Content schemas
from .content_schemas import (
    CreateBlogPostRequest,
    CreateBlogPostResponse,
    TaskStatusResponse,
    BlogDraftResponse,
    DraftsListResponse,
    PublishDraftRequest,
    ApprovalRequest,
    ApprovalResponse,
    PublishDraftResponse,
    GenerateAndPublishRequest,
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

__all__ = [
    # Orchestrator
    "ProcessRequestBody",
    "ApprovalAction",
    "TrainingDataExportRequest",
    "TrainingModelUploadRequest",
    # Quality
    "QualityEvaluationRequest",
    "QualityDimensionsResponse",
    # Task
    "TaskCreateRequest",
    "TaskStatusUpdateRequest",
    "TaskResponse",
    "TaskListResponse",
    # Content
    "CreateBlogPostRequest",
    "CreateBlogPostResponse",
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
]

