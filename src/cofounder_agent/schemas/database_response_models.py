"""
Database Response Models

Comprehensive Pydantic models for all database operations.
Replaces Dict[str, Any] throughout the database_service.py and API routes.
Provides type safety, OpenAPI documentation, and runtime validation.

Models are organized by domain:
- User & Auth
- Tasks & Content
- Posts & Publishing
- Logs & Monitoring
- Financial & Cost Tracking
- Quality Evaluation
- Settings & Configuration
"""

from typing import Optional, Dict, Any, List, Literal, TypeVar, Generic
from datetime import datetime
from pydantic import BaseModel, Field, ConfigDict

T = TypeVar("T")


# ============================================================================
# USER & AUTHENTICATION MODELS
# ============================================================================


class UserResponse(BaseModel):
    """User profile response model."""

    model_config = ConfigDict(from_attributes=True)

    id: str = Field(..., description="User UUID")
    email: str = Field(..., description="User email address")
    username: str = Field(..., description="User username")
    is_active: bool = Field(default=True, description="Whether user account is active")
    created_at: datetime = Field(..., description="Account creation timestamp")
    updated_at: datetime = Field(..., description="Last account update timestamp")


class OAuthAccountResponse(BaseModel):
    """OAuth account linking response model."""

    model_config = ConfigDict(from_attributes=True)

    id: str = Field(..., description="OAuth account UUID")
    user_id: str = Field(..., description="Linked user UUID")
    provider: str = Field(..., description="OAuth provider (github, google, etc.)")
    provider_user_id: str = Field(..., description="User ID from OAuth provider")
    provider_data: Optional[Dict[str, Any]] = Field(
        default=None, description="Additional data from OAuth provider"
    )
    created_at: datetime = Field(..., description="Account linking timestamp")
    last_used: datetime = Field(..., description="Last usage timestamp")


# ============================================================================
# TASK MODELS
# ============================================================================


class TaskResponse(BaseModel):
    """Task response model."""

    model_config = ConfigDict(from_attributes=True)

    id: str = Field(..., description="Task UUID")
    user_id: Optional[str] = Field(None, description="Associated user UUID")
    title: str = Field(..., description="Task title")
    description: Optional[str] = Field(None, description="Task description")
    topic: Optional[str] = Field(None, description="Topic for content generation")
    status: Literal[
        "pending", "in_progress", "completed", "failed", "awaiting_approval", "approved"
    ] = Field(default="pending", description="Task execution status")
    category: Optional[str] = Field(None, description="Task category")
    priority: Optional[int] = Field(default=0, ge=0, le=5, description="Priority level (0-5)")
    task_metadata: Optional[Dict[str, Any]] = Field(
        default=None, description="Additional task metadata (JSON)"
    )
    result: Optional[Dict[str, Any]] = Field(
        default=None, description="Task execution result (JSON)"
    )
    progress: Optional[Dict[str, Any]] = Field(
        default=None, description="Task progress tracking (JSON)"
    )
    created_at: datetime = Field(..., description="Task creation timestamp")
    updated_at: datetime = Field(..., description="Last update timestamp")
    started_at: Optional[datetime] = Field(None, description="Execution start timestamp")
    completed_at: Optional[datetime] = Field(None, description="Completion timestamp")


class TaskCountsResponse(BaseModel):
    """Task counts grouped by status."""

    model_config = ConfigDict(from_attributes=True)

    total: int = Field(default=0, description="Total tasks")
    pending: int = Field(default=0, description="Pending tasks")
    in_progress: int = Field(default=0, description="In-progress tasks")
    completed: int = Field(default=0, description="Completed tasks")
    failed: int = Field(default=0, description="Failed tasks")
    awaiting_approval: int = Field(default=0, description="Awaiting approval tasks")
    approved: int = Field(default=0, description="Approved tasks")


# ============================================================================
# POST & PUBLISHING MODELS
# ============================================================================


class PostResponse(BaseModel):
    """Blog post response model."""

    model_config = ConfigDict(from_attributes=True)

    id: str = Field(..., description="Post UUID")
    title: str = Field(..., description="Post title")
    slug: str = Field(..., description="URL-friendly slug")
    content: str = Field(..., description="Post content (markdown)")
    excerpt: Optional[str] = Field(None, description="Post excerpt")
    featured_image_url: Optional[str] = Field(None, description="Featured image URL")
    cover_image_url: Optional[str] = Field(None, description="Cover image URL")
    author_id: Optional[str] = Field(None, description="Author UUID")
    category_id: Optional[str] = Field(None, description="Category UUID")
    tag_ids: Optional[List[str]] = Field(default=None, description="List of tag UUIDs")
    status: Literal["draft", "published", "archived"] = Field(
        default="draft", description="Publication status"
    )
    seo_title: Optional[str] = Field(None, description="SEO title tag")
    seo_description: Optional[str] = Field(None, description="SEO meta description")
    seo_keywords: Optional[str] = Field(None, description="Comma-separated SEO keywords")
    created_by: Optional[str] = Field(None, description="Creator user UUID")
    updated_by: Optional[str] = Field(None, description="Last editor user UUID")
    created_at: datetime = Field(..., description="Creation timestamp")
    updated_at: datetime = Field(..., description="Last update timestamp")


class CategoryResponse(BaseModel):
    """Content category response model."""

    model_config = ConfigDict(from_attributes=True)

    id: str = Field(..., description="Category UUID")
    name: str = Field(..., description="Category name")
    slug: str = Field(..., description="URL-friendly slug")
    description: Optional[str] = Field(None, description="Category description")


class TagResponse(BaseModel):
    """Content tag response model."""

    model_config = ConfigDict(from_attributes=True)

    id: str = Field(..., description="Tag UUID")
    name: str = Field(..., description="Tag name")
    slug: str = Field(..., description="URL-friendly slug")
    description: Optional[str] = Field(None, description="Tag description")


class AuthorResponse(BaseModel):
    """Author profile response model."""

    model_config = ConfigDict(from_attributes=True)

    id: str = Field(..., description="Author UUID")
    name: str = Field(..., description="Author full name")
    slug: str = Field(..., description="URL-friendly slug")
    email: str = Field(..., description="Author email")


# ============================================================================
# LOG & MONITORING MODELS
# ============================================================================


class LogResponse(BaseModel):
    """Application log response model."""

    model_config = ConfigDict(from_attributes=True)

    id: str = Field(..., description="Log entry UUID")
    agent_name: str = Field(..., description="Agent that created the log")
    level: Literal["debug", "info", "warning", "error", "critical"] = Field(
        ..., description="Log level"
    )
    message: str = Field(..., description="Log message")
    context: Optional[Dict[str, Any]] = Field(
        default=None, description="Additional context (JSON)"
    )
    created_at: datetime = Field(..., description="Log creation timestamp")


class MetricsResponse(BaseModel):
    """System metrics response model."""

    model_config = ConfigDict(from_attributes=True)

    totalTasks: int = Field(default=0, description="Total tasks in system")
    completedTasks: int = Field(default=0, description="Completed tasks")
    failedTasks: int = Field(default=0, description="Failed tasks")
    successRate: float = Field(default=0.0, ge=0.0, le=100.0, description="Success rate percentage")
    avgExecutionTime: float = Field(default=0.0, description="Average execution time in seconds")
    totalCost: float = Field(default=0.0, ge=0.0, description="Total cost in USD")


# ============================================================================
# FINANCIAL & COST TRACKING MODELS
# ============================================================================


class FinancialEntryResponse(BaseModel):
    """Financial tracking entry response model."""

    model_config = ConfigDict(from_attributes=True)

    id: str = Field(..., description="Entry UUID")
    category: str = Field(..., description="Expense category")
    amount: float = Field(..., description="Amount in USD")
    description: Optional[str] = Field(None, description="Entry description")
    tags: Optional[List[str]] = Field(default=None, description="Category tags")
    created_at: datetime = Field(..., description="Creation timestamp")


class FinancialSummaryResponse(BaseModel):
    """Financial summary for a time period."""

    model_config = ConfigDict(from_attributes=True)

    total_entries: int = Field(default=0, description="Number of entries")
    total_amount: float = Field(default=0.0, description="Total amount in USD")
    avg_amount: Optional[float] = Field(None, description="Average amount per entry")
    min_amount: Optional[float] = Field(None, description="Minimum amount")
    max_amount: Optional[float] = Field(None, description="Maximum amount")


class CostLogResponse(BaseModel):
    """Cost tracking for LLM API calls."""

    model_config = ConfigDict(from_attributes=True)

    id: str = Field(..., description="Cost log UUID")
    task_id: str = Field(..., description="Associated task UUID")
    user_id: Optional[str] = Field(None, description="Associated user UUID")
    phase: Literal["research", "outline", "draft", "assess", "refine", "finalize"] = Field(
        ..., description="Execution phase"
    )
    model: str = Field(..., description="LLM model used (gpt-4, claude-3-opus, etc.)")
    provider: Literal["ollama", "openai", "anthropic", "google"] = Field(
        ..., description="LLM provider"
    )
    input_tokens: int = Field(default=0, ge=0, description="Input token count")
    output_tokens: int = Field(default=0, ge=0, description="Output token count")
    total_tokens: int = Field(default=0, ge=0, description="Total token count")
    cost_usd: float = Field(default=0.0, ge=0.0, description="Cost in USD")
    quality_score: Optional[float] = Field(None, ge=0.0, le=5.0, description="Quality rating (0-5)")
    duration_ms: Optional[int] = Field(None, ge=0, description="Execution time in milliseconds")
    success: bool = Field(default=True, description="Whether call succeeded")
    error_message: Optional[str] = Field(None, description="Error details if failed")
    created_at: datetime = Field(..., description="Creation timestamp")
    updated_at: datetime = Field(..., description="Last update timestamp")


class TaskCostBreakdownResponse(BaseModel):
    """Cost breakdown for a specific task."""

    model_config = ConfigDict(from_attributes=True)

    total: float = Field(default=0.0, ge=0.0, description="Total cost in USD")
    research: Optional[Dict[str, Any]] = Field(
        None, description="Research phase costs {cost, model, count}"
    )
    outline: Optional[Dict[str, Any]] = Field(None, description="Outline phase costs")
    draft: Optional[Dict[str, Any]] = Field(None, description="Draft phase costs")
    assess: Optional[Dict[str, Any]] = Field(None, description="Assessment phase costs")
    refine: Optional[Dict[str, Any]] = Field(None, description="Refinement phase costs")
    finalize: Optional[Dict[str, Any]] = Field(None, description="Finalization phase costs")
    entries: Optional[List[CostLogResponse]] = Field(None, description="Detailed cost log entries")


# ============================================================================
# QUALITY EVALUATION MODELS
# ============================================================================


class QualityEvaluationResponse(BaseModel):
    """Quality evaluation response model."""

    model_config = ConfigDict(from_attributes=True)

    id: str = Field(..., description="Evaluation UUID")
    content_id: str = Field(..., description="Evaluated content UUID")
    task_id: Optional[str] = Field(None, description="Associated task UUID")
    overall_score: float = Field(..., ge=0.0, le=10.0, description="Overall quality score (0-10)")
    clarity: float = Field(default=0.0, ge=0.0, le=10.0, description="Clarity score")
    accuracy: float = Field(default=0.0, ge=0.0, le=10.0, description="Accuracy score")
    completeness: float = Field(default=0.0, ge=0.0, le=10.0, description="Completeness score")
    relevance: float = Field(default=0.0, ge=0.0, le=10.0, description="Relevance score")
    seo_quality: float = Field(default=0.0, ge=0.0, le=10.0, description="SEO quality score")
    readability: float = Field(default=0.0, ge=0.0, le=10.0, description="Readability score")
    engagement: float = Field(default=0.0, ge=0.0, le=10.0, description="Engagement score")
    passing: bool = Field(default=False, description="Whether content passed quality threshold")
    feedback: Optional[str] = Field(None, description="Evaluator feedback")
    suggestions: Optional[List[str]] = Field(None, description="Improvement suggestions")
    evaluated_by: str = Field(default="QualityEvaluator", description="Evaluator identifier")
    evaluation_method: str = Field(
        default="pattern-based", description="Evaluation method used"
    )
    evaluation_timestamp: datetime = Field(..., description="Evaluation timestamp")


class QualityImprovementLogResponse(BaseModel):
    """Quality improvement tracking response model."""

    model_config = ConfigDict(from_attributes=True)

    id: str = Field(..., description="Log UUID")
    content_id: str = Field(..., description="Improved content UUID")
    initial_score: float = Field(..., ge=0.0, le=10.0, description="Initial quality score")
    improved_score: float = Field(..., ge=0.0, le=10.0, description="Improved quality score")
    score_improvement: float = Field(
        ..., description="Absolute improvement (improved_score - initial_score)"
    )
    refinement_type: str = Field(
        default="auto-critique", description="Type of refinement applied"
    )
    changes_made: Optional[str] = Field(None, description="Description of changes")
    refinement_timestamp: datetime = Field(..., description="Refinement timestamp")
    passed_after_refinement: bool = Field(
        default=False, description="Whether content passed after refinement"
    )


# ============================================================================
# AGENT STATUS & ORCHESTRATION MODELS
# ============================================================================


class AgentStatusResponse(BaseModel):
    """Agent status response model."""

    model_config = ConfigDict(from_attributes=True)

    id: str = Field(..., description="Status record UUID")
    agent_name: str = Field(..., description="Agent name (content, financial, etc.)")
    status: Literal["idle", "running", "paused", "error"] = Field(
        ..., description="Current agent status"
    )
    last_run: datetime = Field(..., description="Last execution timestamp")
    metadata: Optional[Dict[str, Any]] = Field(
        default=None, description="Agent-specific metadata (JSON)"
    )
    created_at: datetime = Field(..., description="Creation timestamp")
    updated_at: datetime = Field(..., description="Last update timestamp")


class OrchestratorTrainingDataResponse(BaseModel):
    """Training data for orchestrator learning."""

    model_config = ConfigDict(from_attributes=True)

    id: str = Field(..., description="Training data UUID")
    execution_id: str = Field(..., description="Execution identifier")
    user_request: str = Field(..., description="Original user request")
    intent: Optional[str] = Field(None, description="Detected intent")
    business_state: Optional[Dict[str, Any]] = Field(
        None, description="Business context (JSON)"
    )
    execution_result: Optional[str] = Field(None, description="Execution outcome")
    quality_score: Optional[float] = Field(None, ge=0.0, le=10.0, description="Quality score")
    success: bool = Field(default=False, description="Whether execution succeeded")
    tags: Optional[List[str]] = Field(default=None, description="Classification tags")
    source_agent: str = Field(default="content_agent", description="Source agent name")
    created_at: datetime = Field(..., description="Creation timestamp")


# ============================================================================
# SETTINGS & CONFIGURATION MODELS
# ============================================================================


class SettingResponse(BaseModel):
    """Application setting response model."""

    model_config = ConfigDict(from_attributes=True)

    id: str = Field(..., description="Setting UUID")
    key: str = Field(..., description="Setting key identifier")
    value: str = Field(..., description="Setting value (JSON-encoded if complex)")
    category: Optional[str] = Field(None, description="Setting category for grouping")
    display_name: Optional[str] = Field(None, description="Display name for UI")
    description: Optional[str] = Field(None, description="Setting description")
    is_active: bool = Field(default=True, description="Whether setting is active")
    created_at: datetime = Field(..., description="Creation timestamp")
    modified_at: datetime = Field(..., description="Last modification timestamp")


# ============================================================================
# PAGINATED RESPONSE WRAPPER
# ============================================================================


class PaginatedResponse(BaseModel, Generic[T]):
    """Generic paginated response wrapper."""

    model_config = ConfigDict(from_attributes=True)

    total: int = Field(..., description="Total number of items")
    page: int = Field(..., ge=1, description="Current page number")
    limit: int = Field(..., ge=1, le=100, description="Items per page")
    items: List[T] = Field(..., description="Response items")


# ============================================================================
# ERROR RESPONSE MODEL
# ============================================================================


class ErrorResponse(BaseModel):
    """Standard error response model."""

    model_config = ConfigDict(from_attributes=True)

    status: int = Field(..., description="HTTP status code")
    error: str = Field(..., description="Error type")
    message: str = Field(..., description="Error message")
    details: Optional[Dict[str, Any]] = Field(None, description="Additional error details")
    timestamp: datetime = Field(default_factory=datetime.utcnow, description="Error timestamp")
