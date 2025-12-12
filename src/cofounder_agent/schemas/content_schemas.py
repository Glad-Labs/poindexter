"""Content Request/Response Models

Consolidated schemas for content creation, blog posts, drafts,
and approval workflows.
"""

from pydantic import BaseModel, Field
from typing import Optional, List, Dict, Any, Literal
from services.content_router_service import ContentStyle, ContentTone, PublishMode


class CreateBlogPostRequest(BaseModel):
    """Request to create a content task (blog post, social media, email, etc.)"""

    task_type: Literal["blog_post", "social_media", "email", "newsletter"] = Field(
        "blog_post", 
        description="Type of content task to create"
    )
    topic: str = Field(
        ..., 
        min_length=3, 
        max_length=200, 
        description="Content topic/subject",
        examples=["The Future of AI", "E-commerce Best Practices"]
    )
    style: ContentStyle = Field(
        ContentStyle.TECHNICAL, 
        description="Content style (technical, narrative, listicle, educational, thought-leadership)"
    )
    tone: ContentTone = Field(
        ContentTone.PROFESSIONAL, 
        description="Content tone (professional, casual, academic, inspirational)"
    )
    target_length: int = Field(
        1500, 
        ge=200, 
        le=5000, 
        description="Target word count (200-5000 words)",
        examples=[1500, 2000, 3000]
    )
    tags: Optional[List[str]] = Field(
        None, 
        min_items=0,
        max_items=10,
        description="Tags for categorization (max 10)"
    )
    categories: Optional[List[str]] = Field(
        None, 
        min_items=0,
        max_items=5,
        description="Categories for blog posts (max 5)"
    )
    generate_featured_image: bool = Field(
        True, 
        description="Search Pexels for featured image (free)"
    )
    publish_mode: PublishMode = Field(
        PublishMode.DRAFT, 
        description="Draft or publish immediately"
    )
    enhanced: bool = Field(
        False, 
        description="Use SEO enhancement"
    )
    target_environment: str = Field(
        "production", 
        pattern="^(development|staging|production)$",
        description="Target deployment environment (development, staging, production)"
    )
    llm_provider: Optional[str] = Field(
        None, 
        description="Optional: LLM provider override (ollama, openai, anthropic, gemini). If not specified, uses default from config.",
        examples=["ollama", "openai", "anthropic"]
    )
    model: Optional[str] = Field(
        None, 
        description="Optional: Specific model to use (e.g., 'ollama/mistral', 'gpt-4', 'claude-opus'). If not specified, uses default from config.",
        examples=["ollama/mistral", "ollama/phi", "gpt-4", "claude-opus"]
    )

    class Config:
        """Pydantic configuration"""
        json_schema_extra = {
            "example": {
                "task_type": "blog_post",
                "topic": "AI-Powered E-commerce: Trends and Best Practices",
                "style": "technical",
                "tone": "professional",
                "target_length": 2000,
                "tags": ["AI", "E-commerce"],
                "categories": ["Technology"],
                "generate_featured_image": True,
                "publish_mode": "draft",
                "enhanced": True,
                "target_environment": "production",
                "llm_provider": "ollama",
                "model": "ollama/mistral"
            }
        }


class CreateBlogPostResponse(BaseModel):
    """Response from task creation"""

    task_id: str
    task_type: str
    status: str
    topic: str
    created_at: str
    polling_url: str


class TaskStatusResponse(BaseModel):
    """Task status response"""

    task_id: str
    status: str
    progress: Optional[Dict[str, Any]] = None
    result: Optional[Dict[str, Any]] = None
    error: Optional[Dict[str, Any]] = None
    created_at: str


class BlogDraftResponse(BaseModel):
    """Blog draft info"""

    draft_id: str
    title: str
    created_at: str
    status: str
    word_count: int
    summary: Optional[str] = None


class DraftsListResponse(BaseModel):
    """List of drafts"""

    drafts: List[BlogDraftResponse]
    total: int
    limit: int
    offset: int


class PublishDraftRequest(BaseModel):
    """Request to publish a draft"""

    target_environment: str = Field(
        "production", 
        pattern="^(development|staging|production)$",
        description="Target deployment environment: development, staging, or production"
    )
    
    class Config:
        json_schema_extra = {
            "example": {
                "target_environment": "production"
            }
        }


class ApprovalRequest(BaseModel):
    """
    ✅ Phase 5: Human Approval Request
    
    Request from human reviewer to approve or reject a task pending approval.
    Mandatory gate before publishing - requires explicit human decision.
    """
    approved: bool = Field(
        ..., 
        description="True to approve and publish, False to reject"
    )
    human_feedback: str = Field(
        ..., 
        min_length=10, 
        max_length=1000,
        description="Human reviewer feedback (reason for decision) - 10-1000 chars"
    )
    reviewer_id: str = Field(
        ..., 
        min_length=2, 
        max_length=100,
        pattern="^[a-zA-Z0-9._-]+$",
        description="Reviewer username or ID (alphanumeric, dots, dashes, underscores)"
    )

    class Config:
        """Pydantic configuration"""
        json_schema_extra = {
            "example": {
                "approved": True,
                "human_feedback": "Excellent content! Well-researched and engaging. Approved for publication.",
                "reviewer_id": "john.doe"
            }
        }


class ApprovalResponse(BaseModel):
    """Response from approval decision"""
    
    task_id: str
    approval_status: str  # "approved" or "rejected"
    strapi_post_id: Optional[str] = None  # Only if approved and published (UUID or int)
    published_url: Optional[str] = None  # Only if approved and published
    approval_timestamp: str
    reviewer_id: str
    message: str


class PublishDraftResponse(BaseModel):
    """Response from publishing a draft"""

    draft_id: str
    strapi_post_id: int
    published_url: str
    published_at: str
    status: str


class GenerateAndPublishRequest(BaseModel):
    """Request model for content generation and direct publishing"""
    topic: str = Field(..., min_length=3, max_length=200, 
                      description="Topic for content generation (3-200 chars)")
    audience: Optional[str] = Field("General audience", min_length=3, max_length=100,
                                   description="Target audience (3-100 chars)")
    keywords: Optional[List[str]] = Field(None,
                                         description="SEO keywords (max 15)")
    style: Optional[ContentStyle] = Field(ContentStyle.EDUCATIONAL, 
                                         description="Content style (EDUCATIONAL/INFORMATIVE/...)")
    tone: Optional[ContentTone] = Field(ContentTone.PROFESSIONAL, 
                                       description="Content tone (PROFESSIONAL/CASUAL/...)")
    length: Optional[str] = Field("medium", 
                                 pattern="^(short|medium|long)$",
                                 description="Content length: short, medium, or long")
    category: Optional[str] = Field(None, min_length=1, max_length=100,
                                   description="Category ID or name (1-100 chars)")
    tags: Optional[List[str]] = Field(None,
                                     description="Tag names (max 10)")
    auto_publish: Optional[bool] = Field(False, description="Immediately publish to site")
    
    class Config:
        json_schema_extra = {
            "example": {
                "topic": "How to Implement AI-Driven Content Generation",
                "audience": "Software developers and content creators",
                "keywords": ["AI", "content", "generation", "automation"],
                "style": "EDUCATIONAL",
                "tone": "PROFESSIONAL",
                "length": "medium",
                "category": "ai-technology",
                "tags": ["AI", "Tutorial", "Best-Practices"],
                "auto_publish": False
            }
        }
