"""Metrics and Health Models

Consolidated schemas for cost, health, and performance metrics.
"""

from pydantic import BaseModel, Field
from typing import Optional, Dict, List, Any


class CostMetric(BaseModel):
    """Individual cost metric"""

    model_name: str
    provider: str
    tokens_used: int
    cost_usd: float
    timestamp: str


class CostsResponse(BaseModel):
    """Cost metrics response"""

    total_cost: float
    total_tokens: int
    by_model: List[Dict[str, Any]]
    by_provider: Dict[str, float]
    period: str
    updated_at: str


class HealthMetrics(BaseModel):
    """Health check metrics"""

    status: str
    uptime_seconds: float
    active_tasks: int
    completed_tasks: int
    failed_tasks: int
    api_version: str


class PerformanceMetrics(BaseModel):
    """Performance metrics"""

    avg_response_time_ms: float
    requests_per_minute: float
    error_rate: float
    cache_hit_rate: float
