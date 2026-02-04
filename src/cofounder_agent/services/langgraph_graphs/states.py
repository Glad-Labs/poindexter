"""Shared state definitions for all LangGraph workflows"""

import operator
from datetime import datetime
from typing import Annotated, Dict, Optional, TypedDict

# ============================================================================
# CONTENT PIPELINE STATES
# ============================================================================


class ContentPipelineState(TypedDict):
    """State for blog post creation workflow"""

    # INPUT
    topic: str
    keywords: list[str]
    audience: str
    tone: str  # "professional", "casual", "academic"
    word_count: int  # target
    request_id: str
    user_id: str

    # MODEL SELECTION (per-phase)
    models_by_phase: Optional[Dict[str, str]]  # {"research": "ollama", "draft": "gpt-4", ...}
    quality_preference: Optional[str]  # "fast", "balanced", "quality" (for auto-selection)

    # PROCESSING
    research_notes: str
    outline: str
    draft: str
    final_content: str

    # QUALITY TRACKING
    quality_score: float
    quality_feedback: str
    passed_quality: bool
    refinement_count: int
    max_refinements: int

    # METADATA
    seo_score: float
    metadata: dict
    tags: list[str]

    # COST TRACKING
    cost_breakdown: Dict[str, float]  # {"research": 0.0, "draft": 0.0015, ...}
    total_cost: float  # Sum of all phase costs

    # OUTPUT
    task_id: Optional[str]
    status: str  # "pending", "in_progress", "completed", "failed"
    created_at: datetime
    completed_at: Optional[datetime]

    # TRACKING (messages accumulate)
    messages: Annotated[list[dict], operator.add]
    errors: Annotated[list[str], operator.add]


class FinancialAnalysisState(TypedDict):
    """State for financial analysis workflow"""

    # INPUT
    ticker: str
    company_name: str
    analysis_type: str  # "quarterly", "yearly", "valuation"
    request_id: str

    # PROCESSING
    financial_data: dict
    market_context: str
    analysis: str
    risk_assessment: str
    recommendation: str

    # QUALITY
    analyst_review_required: bool
    analyst_feedback: Optional[str]
    approved: bool

    # OUTPUT
    report_url: Optional[str]
    status: str
    created_at: datetime


class ContentReviewState(TypedDict):
    """State for human-in-the-loop review"""

    content_id: str
    content: str
    reviewer_id: Optional[str]
    review_status: str  # "pending", "approved", "rejected"
    feedback: str
    revision_count: int
    messages: Annotated[list[dict], operator.add]
