"""
Task Management Schemas

Consolidates all Pydantic models for task management endpoints
"""

from typing import Optional, Dict, Any, List, Literal
from pydantic import BaseModel, Field
from schemas.unified_task_response import UnifiedTaskResponse


class UnifiedTaskRequest(BaseModel):
    """
    Unified task creation request - single endpoint for all task types.
    
    Routes to appropriate handler based on task_type.
    Extensible for future task types (business_analytics, data_retrieval, etc.)
    """

    # REQUIRED: Task type determines routing
    task_type: Literal[
        "blog_post", 
        "social_media", 
        "email", 
        "newsletter",
        "business_analytics",
        "data_retrieval",
        "market_research",
        "financial_analysis"
    ] = Field(
        "blog_post",
        description="Type of task: blog_post, social_media, email, newsletter, business_analytics, data_retrieval, etc."
    )

    # COMMON FIELDS - All task types
    topic: str = Field(
        ...,
        min_length=3,
        max_length=200,
        description="Task topic/subject/query",
        examples=["AI Trends in Healthcare", "Q4 Revenue Analysis", "Competitor Pricing Strategy"]
    )

    # CONTENT-SPECIFIC FIELDS (for blog_post, social_media, email, newsletter)
    style: Optional[Literal["technical", "narrative", "listicle", "educational", "thought-leadership"]] = Field(
        "narrative",
        description="Content style (blog_post, social_media, email only)"
    )
    tone: Optional[Literal["professional", "casual", "academic", "inspirational"]] = Field(
        "professional",
        description="Content tone (blog_post, social_media, email only)"
    )
    target_length: Optional[int] = Field(
        1500,
        ge=200,
        le=5000,
        description="Target word count for content (200-5000, blog_post only)"
    )
    generate_featured_image: Optional[bool] = Field(
        True,
        description="Search for featured image (blog_post only)"
    )
    tags: Optional[List[str]] = Field(
        None,
        min_items=0,
        max_items=10,
        description="Tags for categorization (max 10)"
    )

    # SOCIAL MEDIA SPECIFIC
    platforms: Optional[List[str]] = Field(
        None,
        description="Target platforms for social_media tasks (twitter, linkedin, instagram, etc.)"
    )

    # BUSINESS ANALYTICS SPECIFIC
    metrics: Optional[List[str]] = Field(
        None,
        description="Metrics to analyze (revenue, churn, conversion_rate, etc.)"
    )
    time_period: Optional[str] = Field(
        None,
        description="Analysis time period (last_month, last_quarter, ytd, custom)"
    )
    business_context: Optional[Dict[str, Any]] = Field(
        None,
        description="Business context for analytics (industry, size, goals)"
    )

    # DATA RETRIEVAL SPECIFIC
    data_sources: Optional[List[str]] = Field(
        None,
        description="Data sources to query (api, database, csv, etc.)"
    )
    filters: Optional[Dict[str, Any]] = Field(
        None,
        description="Data filters and query parameters"
    )

    # COMMON OPTIONAL
    category: Optional[str] = Field(
        "general",
        description="Content category",
        max_length=50
    )
    models_by_phase: Optional[Dict[str, str]] = Field(
        None,
        description="Per-phase model selection (research, creative, qa, etc.)"
    )
    quality_preference: Optional[Literal["fast", "balanced", "quality"]] = Field(
        "balanced",
        description="Quality vs speed preference"
    )
    metadata: Optional[Dict[str, Any]] = Field(
        None,
        description="Additional metadata for task"
    )

    class Config:
        json_schema_extra = {
            "examples": [
                {
                    "task_type": "blog_post",
                    "topic": "AI Trends in Healthcare 2025",
                    "style": "technical",
                    "tone": "professional",
                    "target_length": 2000,
                    "generate_featured_image": True,
                    "tags": ["AI", "Healthcare"],
                    "quality_preference": "balanced"
                },
                {
                    "task_type": "social_media",
                    "topic": "New Product Launch Campaign",
                    "platforms": ["twitter", "linkedin"],
                    "tone": "casual",
                    "tags": ["marketing", "product"]
                },
                {
                    "task_type": "business_analytics",
                    "topic": "Revenue Analysis Q4 2025",
                    "metrics": ["revenue", "churn_rate", "customer_acquisition"],
                    "time_period": "last_quarter",
                    "business_context": {"industry": "SaaS", "size": "mid-market"}
                },
                {
                    "task_type": "data_retrieval",
                    "topic": "Retrieve customer data for ML training",
                    "data_sources": ["postgres_db", "s3_bucket"],
                    "filters": {"date_range": "last_6_months", "status": "active"}
                }
            ]
        }


class ContentConstraints(BaseModel):
    """Content generation constraints - Tier 1 & 2 features"""

    # Tier 1: Basic constraints
    word_count: int = Field(
        default=1500, ge=300, le=5000, description="Target word count for entire content (300-5000)"
    )
    writing_style: Literal[
        "technical", "narrative", "listicle", "educational", "thought-leadership"
    ] = Field(default="technical", description="Writing style preference")

    # Tier 2: Fine-grained control
    word_count_tolerance: int = Field(
        default=10,
        ge=5,
        le=20,
        description="Acceptable variance from target word count (percentage, 5-20%)",
    )
    per_phase_overrides: Optional[Dict[str, int]] = Field(
        default=None,
        description="Override word count targets per phase (research, outline, draft, etc.)",
    )
    strict_mode: bool = Field(
        default=False,
        description="If True, fail task if constraints not met; if False, warn but continue",
    )

    class Config:
        json_schema_extra = {
            "example": {
                "word_count": 2000,
                "writing_style": "narrative",
                "word_count_tolerance": 10,
                "per_phase_overrides": {"research": 300, "draft": 1200},
                "strict_mode": False,
            }
        }


class TaskCreateRequest(BaseModel):
    """Schema for creating a new task"""

    task_name: str = Field(
        ..., min_length=3, max_length=200, description="Name of the task (3-200 chars)"
    )
    topic: str = Field(
        ..., min_length=3, max_length=200, description="Blog post topic (3-200 chars)"
    )
    primary_keyword: str = Field(
        default="", max_length=100, description="Primary SEO keyword (max 100 chars)"
    )
    target_audience: str = Field(
        default="", max_length=100, description="Target audience (max 100 chars)"
    )
    category: str = Field(
        default="general", max_length=50, description="Content category (max 50 chars)"
    )
    writing_style_id: Optional[str] = Field(
        default=None, description="UUID of the writing sample to use for style guidance (optional)"
    )
    model_selections: Optional[Dict[str, str]] = Field(
        default_factory=dict,
        description="Per-phase model selections (research, outline, draft, assess, refine, finalize)",
    )
    quality_preference: Optional[str] = Field(
        default="balanced",
        pattern="^(fast|balanced|quality)$",
        description="Quality preference: fast (cheapest), balanced, or quality (best)",
    )
    estimated_cost: Optional[float] = Field(
        default=0.0, ge=0.0, description="Estimated task cost in USD"
    )
    # NEW: Content constraints
    content_constraints: Optional[ContentConstraints] = Field(
        default_factory=ContentConstraints,
        description="Content generation constraints (word count, writing style, etc.)",
    )
    metadata: Optional[Dict[str, Any]] = Field(
        default_factory=dict, description="Additional metadata"
    )

    class Config:
        json_schema_extra = {
            "example": {
                "task_name": "Blog Post - AI in Healthcare",
                "topic": "How AI is Transforming Healthcare",
                "primary_keyword": "AI healthcare",
                "target_audience": "Healthcare professionals",
                "category": "healthcare",
                "writing_style_id": "550e8400-e29b-41d4-a716-446655440000",
                "model_selections": {
                    "research": "ollama",
                    "outline": "ollama",
                    "draft": "gpt-3.5-turbo",
                    "assess": "gpt-4",
                    "refine": "gpt-4",
                    "finalize": "gpt-4",
                },
                "quality_preference": "balanced",
                "estimated_cost": 0.015,
                "metadata": {"priority": "high"},
            }
        }


class TaskStatusUpdateRequest(BaseModel):
    """Schema for updating task status"""

    status: str = Field(
        ...,
        pattern="^(pending|in_progress|completed|failed|cancelled)$",
        description="New task status",
    )
    result: Optional[Dict[str, Any]] = Field(None, description="Task result/output")
    metadata: Optional[Dict[str, Any]] = Field(None, description="Additional metadata")

    class Config:
        json_schema_extra = {
            "example": {
                "status": "completed",
                "result": {"content": "Generated blog post..."},
                "metadata": {"execution_time": 45.2},
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

    tasks: List[UnifiedTaskResponse]
    total: int
    offset: int
    limit: int


class MetricsResponse(BaseModel):
    """Schema for aggregated metrics"""

    total_tasks: int = Field(..., description="Total tasks created")
    completed_tasks: int = Field(..., description="Successfully completed tasks")
    failed_tasks: int = Field(..., description="Failed tasks")
    pending_tasks: int = Field(..., description="Pending/queued tasks")
    success_rate: float = Field(..., description="Success rate percentage (0-100)")
    avg_execution_time: float = Field(..., description="Average execution time in seconds")
    total_cost: float = Field(..., description="Total estimated cost in USD")

    class Config:
        json_schema_extra = {
            "example": {
                "total_tasks": 150,
                "completed_tasks": 120,
                "failed_tasks": 5,
                "pending_tasks": 25,
                "success_rate": 80.0,
                "avg_execution_time": 45.2,
                "total_cost": 125.50,
            }
        }


class IntentTaskRequest(BaseModel):
    """Request for natural language task creation."""

    user_input: str = Field(..., description="Natural language task description")
    user_context: Optional[Dict[str, Any]] = Field(
        None, description="User context (preferences, settings)"
    )
    business_metrics: Optional[Dict[str, Any]] = Field(
        None, description="Budget, deadline, quality preference"
    )


class TaskIntentResponse(BaseModel):
    """Response from intent detection and planning."""

    task_id: Optional[str] = Field(None, description="Temp task ID for confirmation")
    intent_request: Dict[str, Any] = Field(
        ..., description="Parsed intent (task_type, parameters, subtasks)"
    )
    execution_plan: Dict[str, Any] = Field(..., description="Execution plan summary for UI")
    ready_to_execute: bool = Field(True, description="Whether user can proceed with execution")
    warnings: Optional[List[str]] = Field(None, description="Warnings (e.g., 'No QA review')")


class TaskConfirmRequest(BaseModel):
    """Request to confirm and execute a task from intent plan."""

    intent_request: Dict[str, Any] = Field(..., description="Original intent request")
    execution_plan: Dict[str, Any] = Field(..., description="Execution plan (full version)")
    user_confirmed: bool = Field(True, description="User confirmed the plan")
    modifications: Optional[Dict[str, Any]] = Field(None, description="User modifications to plan")


class TaskConfirmResponse(BaseModel):
    """Response from task confirmation and creation."""

    task_id: str
    status: str
    message: str
    execution_plan_id: str
