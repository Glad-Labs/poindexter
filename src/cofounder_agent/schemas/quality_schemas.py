"""
Quality Assessment Schemas

Consolidates all Pydantic models for quality evaluation endpoints
"""

from datetime import datetime
from typing import Any, Dict, List, Optional

from pydantic import BaseModel, Field


class QualityEvaluationRequest(BaseModel):
    """Request to evaluate content quality"""

    content: str = Field(..., min_length=10, max_length=50000, description="Content to evaluate")
    topic: Optional[str] = Field(None, description="Topic or subject matter (improves accuracy)")
    keywords: Optional[List[str]] = Field(None, description="Keywords to check for presence")
    method: str = Field(
        "pattern-based", description="Evaluation method: pattern-based, llm-based, or hybrid"
    )
    store_result: bool = Field(True, description="Store evaluation result in database")

    class Config:
        json_schema_extra = {
            "example": {
                "content": "Your content here...",
                "topic": "AI in Healthcare",
                "keywords": ["AI", "healthcare", "machine learning"],
                "method": "pattern-based",
            }
        }


class QualityDimensionsResponse(BaseModel):
    """Quality dimensions response"""

    clarity: float
    accuracy: float
    completeness: float
    relevance: float
    seo_quality: float
    readability: float
    engagement: float


class QualityEvaluationResponse(BaseModel):
    """Response from quality evaluation"""

    overall_score: float
    passing: bool
    dimensions: QualityDimensionsResponse
    feedback: str
    suggestions: List[str]
    evaluation_method: str
    content_length: int
    word_count: int
    evaluated_at: datetime


class BatchQualityRequest(BaseModel):
    """Request to evaluate multiple content items"""

    items: List[Dict[str, Any]] = Field(..., description="List of content items to evaluate")

    class Config:
        json_schema_extra = {
            "example": {
                "items": [
                    {"content": "First content...", "topic": "Topic 1"},
                    {"content": "Second content...", "topic": "Topic 2"},
                ]
            }
        }
