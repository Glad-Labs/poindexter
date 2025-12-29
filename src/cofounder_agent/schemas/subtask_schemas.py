"""Content Subtask Pipeline Models

Consolidated schemas for individual pipeline stage execution.
"""

from pydantic import BaseModel, Field
from typing import Optional, List, Dict, Any


class ResearchSubtaskRequest(BaseModel):
    """Request to run research stage independently."""

    topic: str = Field(..., description="Topic to research")
    keywords: List[str] = Field(default_factory=list, description="Keywords to focus on")
    parent_task_id: Optional[str] = Field(None, description="Parent task ID for chaining")


class CreativeSubtaskRequest(BaseModel):
    """Request to run creative stage independently."""

    topic: str = Field(..., description="Topic for content")
    research_output: Optional[str] = Field(None, description="Output from research stage")
    style: Optional[str] = Field("professional", description="Content style")
    tone: Optional[str] = Field("informative", description="Content tone")
    target_length: Optional[int] = Field(2000, description="Target word count")
    parent_task_id: Optional[str] = Field(None, description="Parent task ID for chaining")


class QASubtaskRequest(BaseModel):
    """Request to run QA stage independently."""

    topic: str = Field(..., description="Original topic")
    creative_output: str = Field(..., description="Content to review")
    research_output: Optional[str] = Field(None, description="Original research for context")
    style: Optional[str] = Field("professional")
    tone: Optional[str] = Field("informative")
    max_iterations: int = Field(2, ge=1, le=5, description="Max refinement iterations")
    parent_task_id: Optional[str] = Field(None, description="Parent task ID for chaining")


class ImageSubtaskRequest(BaseModel):
    """Request to run image search independently."""

    topic: str = Field(..., description="Topic for image search")
    content: Optional[str] = Field(None, description="Content context for image selection")
    number_of_images: int = Field(1, ge=1, le=5, description="How many images to find")
    parent_task_id: Optional[str] = Field(None, description="Parent task ID for chaining")


class FormatSubtaskRequest(BaseModel):
    """Request to run formatting stage independently."""

    topic: str = Field(..., description="Content topic")
    content: str = Field(..., description="Content to format")
    featured_image_url: Optional[str] = Field(None, description="Featured image URL")
    tags: List[str] = Field(default_factory=list, description="Content tags")
    category: Optional[str] = Field(None, description="Content category")
    parent_task_id: Optional[str] = Field(None, description="Parent task ID for chaining")


class SubtaskResponse(BaseModel):
    """Response from subtask execution."""

    subtask_id: str
    stage: str  # "research", "creative", etc.
    parent_task_id: Optional[str]
    status: str  # "completed", "pending", "failed"
    result: Dict[str, Any]  # Stage-specific output
    metadata: Dict[str, Any]  # Execution metrics (duration, tokens, cost)
