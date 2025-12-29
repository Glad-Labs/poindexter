"""
Orchestrator Request/Response Schemas

Consolidates all Pydantic models for orchestration endpoints
"""

from typing import Optional, Dict, Any, List
from pydantic import BaseModel, Field


class ProcessRequestBody(BaseModel):
    """Natural language request for orchestrator"""

    prompt: str = Field(
        ...,
        min_length=10,
        max_length=2000,
        description="Natural language description of what you want",
    )
    context: Optional[Dict[str, Any]] = Field(
        None, description="Optional context (audience, keywords, tone, etc.)"
    )
    auto_approve: bool = Field(False, description="Auto-approve if quality score >= 0.85")


class ApprovalAction(BaseModel):
    """Approve and publish action"""

    approved: bool
    publish_to_channels: List[str] = Field(
        ["blog"], description="Channels to publish to: blog, linkedin, twitter, email"
    )
    modifications: Optional[Dict[str, Any]] = Field(
        None, description="Optional modifications before publishing"
    )


class TrainingDataExportRequest(BaseModel):
    """Export training data"""

    format: str = Field("jsonl", description="Format: jsonl, csv")
    min_quality_score: Optional[float] = Field(None, description="Minimum quality score filter")
    limit: int = Field(1000, description="Max examples to export")


class TrainingModelUploadRequest(BaseModel):
    """Upload fine-tuned model"""

    model_name: str = Field(..., description="Name of the fine-tuned model")
    model_type: str = Field(
        ..., description="Type: task-router, content-generator, quality-evaluator"
    )
    description: Optional[str] = Field(None)
