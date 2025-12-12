"""Natural Language Content Processing Models

Consolidated schemas for natural language content operations.
"""

from pydantic import BaseModel, Field
from typing import Optional, Dict, Any
from datetime import datetime


class NaturalLanguageRequest(BaseModel):
    """Natural language request for content operations"""
    prompt: str = Field(
        ...,
        min_length=10,
        max_length=2000,
        description="Natural language description of what you want to create"
    )
    context: Optional[Dict[str, Any]] = Field(
        None,
        description="Optional context (topic, keywords, audience, etc.)"
    )
    auto_quality_check: bool = Field(
        True,
        description="Automatically evaluate quality of generated content"
    )
    
    class Config:
        json_schema_extra = {
            "example": {
                "prompt": "Create a blog post about machine learning applications in healthcare",
                "context": {
                    "audience": "technical professionals",
                    "keywords": ["machine learning", "healthcare", "AI"]
                },
                "auto_quality_check": True
            }
        }


class RefineContentRequest(BaseModel):
    """Request to refine existing content"""
    feedback: str = Field(
        ...,
        min_length=10,
        max_length=1000,
        description="Feedback on what to improve"
    )
    focus_area: Optional[str] = Field(
        None,
        description="Specific area to focus on (clarity, accuracy, engagement, etc.)"
    )


class NaturalLanguageResponse(BaseModel):
    """Response from natural language content endpoint"""
    request_id: str
    status: str  # pending, executing, completed, failed
    request_type: str
    task_id: Optional[str]
    output: Optional[str]
    quality: Optional[Dict[str, Any]]
    message: str
    created_at: datetime
