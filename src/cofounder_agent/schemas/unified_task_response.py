"""
Unified Task Response Schema

Provides a single, consolidated response format for all task-related endpoints
via the /api/tasks route.

This ensures frontend clients see consistent data format.

Benefits:
- Single source of truth for task response format
- Consistent field naming across all endpoints
- Support for all task types (blog posts, social media, emails, etc.)
- Backward compatible with existing frontend clients
"""

from pydantic import BaseModel, Field
from typing import Optional, Dict, Any, List, Literal
from datetime import datetime


class ProgressInfo(BaseModel):
    """Real-time progress information for tasks in progress"""

    stage: Optional[str] = Field(None, description="Current pipeline stage")
    percentage: Optional[int] = Field(None, ge=0, le=100, description="Progress percentage 0-100")
    message: Optional[str] = Field(None, description="Status message")
    node: Optional[str] = Field(None, description="LangGraph node name (alias for stage)")

    class Config:
        json_schema_extra = {
            "example": {
                "stage": "draft",
                "percentage": 50,
                "message": "Drafting content...",
                "node": "draft",
            }
        }


class CostBreakdown(BaseModel):
    """Cost breakdown by pipeline phase"""

    research: Optional[float] = 0.0
    outline: Optional[float] = 0.0
    draft: Optional[float] = 0.0
    assess: Optional[float] = 0.0
    refine: Optional[float] = 0.0
    finalize: Optional[float] = 0.0
    total: float = 0.0

    class Config:
        json_schema_extra = {
            "example": {
                "research": 0.001,
                "outline": 0.0005,
                "draft": 0.002,
                "assess": 0.0015,
                "refine": 0.001,
                "finalize": 0.0005,
                "total": 0.0075,
            }
        }


class ModelSelection(BaseModel):
    """Model selection by pipeline phase"""

    research: Optional[str] = None
    outline: Optional[str] = None
    draft: Optional[str] = None
    assess: Optional[str] = None
    refine: Optional[str] = None
    finalize: Optional[str] = None

    class Config:
        json_schema_extra = {
            "example": {
                "research": "mistral",
                "outline": "mistral",
                "draft": "gpt-4",
                "assess": "claude-3-sonnet",
                "refine": "gpt-4",
                "finalize": "mistral",
            }
        }


class TaskResultContent(BaseModel):
    """Content result fields for completed tasks"""

    content: Optional[str] = Field(None, description="Generated content")
    excerpt: Optional[str] = Field(None, description="Content excerpt")
    featured_image_url: Optional[str] = Field(None, description="URL of featured image")
    featured_image_data: Optional[Dict[str, Any]] = Field(
        None, description="Featured image metadata"
    )
    qa_feedback: Optional[str] = Field(None, description="QA feedback")
    quality_score: Optional[float] = Field(None, ge=0, le=100, description="Quality score 0-100")
    seo_title: Optional[str] = Field(None, description="SEO-optimized title")
    seo_description: Optional[str] = Field(None, description="SEO meta description")
    seo_keywords: Optional[List[str]] = Field(None, description="SEO keywords")

    class Config:
        json_schema_extra = {
            "example": {
                "content": "# Blog Post Title\n\nContent here...",
                "excerpt": "Short summary of the content",
                "featured_image_url": "https://images.pexels.com/...",
                "quality_score": 92.5,
                "seo_title": "SEO-optimized title | Your Site",
                "seo_description": "Brief meta description for search engines",
                "seo_keywords": ["keyword1", "keyword2"],
            }
        }


class UnifiedTaskResponse(BaseModel):
    """
    Unified response schema for all task operations.

    Consolidated format supports /api/tasks endpoint.
    Provides consistent structure for task creation, retrieval, and updates.
    """

    # ========================================================================
    # IDENTIFICATION & METADATA
    # ========================================================================
    id: Optional[str] = Field(None, description="Task ID (UUID)")
    task_id: Optional[str] = Field(None, description="Task ID (alias for compatibility)")
    request_id: Optional[str] = Field(None, description="Request ID for WebSocket tracking")

    # Task Classification
    task_name: Optional[str] = Field(None, description="Task name/title")
    task_type: str = Field(
        "blog_post", description="Type of task (blog_post, social_media, email, etc.)"
    )
    request_type: Optional[str] = Field("content_generation", description="Request type")

    # Content Metadata
    topic: Optional[str] = Field(None, description="Content topic/subject")
    primary_keyword: Optional[str] = Field(None, description="Primary SEO keyword")
    target_audience: Optional[str] = Field(None, description="Target audience")
    category: Optional[str] = Field(None, description="Content category")
    tags: Optional[List[str]] = Field(None, description="Associated tags")

    # ========================================================================
    # STATUS & PROGRESS
    # ========================================================================
    status: str = Field(
        ..., description="Task status (pending, generating, completed, failed, etc.)"
    )
    approval_status: Optional[str] = Field(
        None, description="Approval status (pending, approved, rejected)"
    )
    publish_status: Optional[str] = Field(None, description="Publish status (draft, published)")

    # Real-time Progress (for in-progress tasks)
    progress: Optional[ProgressInfo] = Field(None, description="Real-time progress info")
    stage: Optional[str] = Field(None, description="Current pipeline stage")
    percentage: Optional[int] = Field(None, ge=0, le=100, description="Progress percentage")
    message: Optional[str] = Field(None, description="Status message")

    # ========================================================================
    # CONTENT GENERATION PARAMETERS
    # ========================================================================
    style: Optional[str] = Field(None, description="Writing style (technical, narrative, etc.)")
    tone: Optional[str] = Field(None, description="Writing tone (professional, casual, etc.)")
    target_length: Optional[int] = Field(None, description="Target word count")

    # Model Selection & Costs
    quality_preference: Optional[str] = Field(
        None, description="Quality preference (budget, balanced, quality, premium)"
    )
    models_by_phase: Optional[ModelSelection] = Field(
        None, description="Models selected for each phase"
    )
    model_used: Optional[str] = Field(None, description="Primary model used")
    estimated_cost: Optional[float] = Field(None, ge=0, description="Estimated cost in USD")
    cost_breakdown: Optional[CostBreakdown] = Field(None, description="Cost breakdown by phase")

    # ========================================================================
    # RESULTS (For completed/approved tasks)
    # ========================================================================
    result: Optional[TaskResultContent] = Field(None, description="Task result content")
    content: Optional[str] = Field(None, description="Generated content (alias for result.content)")
    excerpt: Optional[str] = Field(None, description="Content excerpt")
    featured_image_url: Optional[str] = Field(None, description="Featured image URL")
    featured_image_data: Optional[Dict[str, Any]] = Field(
        None, description="Featured image metadata"
    )
    quality_score: Optional[float] = Field(None, ge=0, le=100, description="Quality score")
    seo_title: Optional[str] = Field(None, description="SEO title")
    seo_description: Optional[str] = Field(None, description="SEO description")
    seo_keywords: Optional[List[str]] = Field(None, description="SEO keywords")

    # ========================================================================
    # CONSTRAINT COMPLIANCE (For generated content)
    # ========================================================================
    constraint_compliance: Optional[Dict[str, Any]] = Field(
        None, description="Word count and writing style compliance metrics"
    )

    # ========================================================================
    # ERROR HANDLING (For failed tasks)
    # ========================================================================
    error_message: Optional[str] = Field(None, description="Error message if task failed")
    error_details: Optional[Dict[str, Any]] = Field(None, description="Detailed error information")

    # ========================================================================
    # TIMESTAMPS
    # ========================================================================
    created_at: datetime | str = Field(..., description="Task creation timestamp (ISO format)")
    updated_at: datetime | str = Field(..., description="Last update timestamp (ISO format)")
    started_at: Optional[datetime | str] = Field(None, description="Execution start time")
    completed_at: Optional[datetime | str] = Field(None, description="Completion time")

    # ========================================================================
    # BACKEND METADATA (For debugging)
    # ========================================================================
    agent_id: Optional[str] = Field(None, description="Agent ID that executed task")
    metadata: Optional[Dict[str, Any]] = Field(None, description="Additional metadata")
    task_metadata: Optional[Dict[str, Any]] = Field(None, description="Task-specific metadata")
    polling_url: Optional[str] = Field(None, description="URL to poll for status")

    class Config:
        json_schema_extra = {
            "example": {
                "id": "550e8400-e29b-41d4-a716-446655440000",
                "task_name": "Blog Post - The Future of AI",
                "task_type": "blog_post",
                "status": "completed",
                "approval_status": "approved",
                "publish_status": "draft",
                "topic": "The Future of AI in Healthcare",
                "primary_keyword": "AI healthcare future",
                "target_audience": "Healthcare professionals",
                "category": "technology",
                "style": "technical",
                "tone": "professional",
                "target_length": 2000,
                "quality_preference": "quality",
                "models_by_phase": {
                    "research": "mistral",
                    "outline": "mistral",
                    "draft": "gpt-4",
                    "assess": "claude-3-sonnet",
                    "refine": "gpt-4",
                    "finalize": "mistral",
                },
                "estimated_cost": 0.0125,
                "cost_breakdown": {
                    "research": 0.001,
                    "outline": 0.0005,
                    "draft": 0.005,
                    "assess": 0.003,
                    "refine": 0.002,
                    "finalize": 0.0005,
                    "total": 0.0125,
                },
                "content": "# The Future of AI in Healthcare\n\nContent here...",
                "excerpt": "Exploring how AI is transforming healthcare...",
                "featured_image_url": "https://images.pexels.com/...",
                "quality_score": 94.2,
                "seo_title": "The Future of AI in Healthcare | Your Blog",
                "seo_description": "Discover how AI is revolutionizing healthcare",
                "seo_keywords": ["AI", "healthcare", "future"],
                "created_at": "2025-12-22T10:30:00Z",
                "updated_at": "2025-12-22T10:35:45Z",
                "completed_at": "2025-12-22T10:35:00Z",
            }
        }


# Aliases for backward compatibility
TaskResponse = UnifiedTaskResponse
CreateBlogPostResponse = UnifiedTaskResponse
BlogPostResponse = UnifiedTaskResponse
