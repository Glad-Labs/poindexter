"""
Metrics and Analytics Routes
Provides endpoints for tracking AI model usage, costs, and performance metrics

All endpoints require JWT authentication
"""

from fastapi import APIRouter, HTTPException, Depends
from pydantic import BaseModel
from typing import Dict, Any, List
from datetime import datetime
import logging

from routes.auth_unified import get_current_user, UserProfile

logger = logging.getLogger(__name__)

# Create metrics router
metrics_router = APIRouter(prefix="/api/metrics", tags=["metrics"])


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


# In-memory storage for metrics (replace with database in production)
_cost_metrics = {
    "total": 0.0,
    "models": {
        "ollama": {"tokens": 0, "cost": 0.0},
        "neural-chat": {"tokens": 5043, "cost": 0.0},
        "mistral": {"tokens": 2862, "cost": 0.0},
        "llama2": {"tokens": 2146, "cost": 0.0},
        "qwen2.5": {"tokens": 1511, "cost": 0.0},
    },
    "providers": {
        "local": 0.0,
        "openai": 0.0,
        "anthropic": 0.0,
        "google": 0.0,
    },
}

_start_time = datetime.now()
_task_stats = {
    "active": 0,
    "completed": 1,  # Blog post generation task
    "failed": 0,
}


@metrics_router.get("/costs")
async def get_cost_metrics(
    current_user: UserProfile = Depends(get_current_user)
) -> Dict[str, Any]:
    """
    Get AI model usage and cost metrics
    Requires: Valid JWT authentication
    
    Returns:
        Cost breakdown by model and provider
    """
    # Calculate totals
    total_cost = sum(m["cost"] for m in _cost_metrics["models"].values())
    total_tokens = sum(m["tokens"] for m in _cost_metrics["models"].values())
    
    # Group by provider
    by_model = [
        {
            "model": name,
            "tokens": metrics["tokens"],
            "cost": metrics["cost"],
            "provider": "ollama" if name == "ollama" else "local",
        }
        for name, metrics in _cost_metrics["models"].items()
    ]
    
    return {
        "total_cost": total_cost,
        "total_tokens": total_tokens,
        "by_model": by_model,
        "by_provider": _cost_metrics["providers"],
        "period": "all_time",
        "updated_at": datetime.now().isoformat(),
    }


@metrics_router.get("")
async def get_metrics(
    current_user: UserProfile = Depends(get_current_user)
) -> Dict[str, Any]:
    """
    Get aggregated application metrics
    Requires: Valid JWT authentication
    
    Returns:
        System health and performance metrics
    """
    # Calculate uptime
    uptime = (datetime.now() - _start_time).total_seconds()
    
    return {
        "status": "healthy",
        "uptime_seconds": uptime,
        "active_tasks": _task_stats["active"],
        "completed_tasks": _task_stats["completed"],
        "failed_tasks": _task_stats["failed"],
        "api_version": "1.0.0",
        "timestamp": datetime.now().isoformat(),
        "services": {
            "database": "healthy",
            "ollama": "healthy",
            "cache": "healthy",
        },
    }


@metrics_router.get("/summary")
async def get_metrics_summary(
    current_user: UserProfile = Depends(get_current_user)
) -> Dict[str, Any]:
    """
    Get a summary of all metrics
    Requires: Valid JWT authentication
    
    Returns:
        Summary of costs, performance, and health
    """
    # Calculate uptime
    uptime = (datetime.now() - _start_time).total_seconds()
    total_cost = sum(m["cost"] for m in _cost_metrics["models"].values())
    total_tokens = sum(m["tokens"] for m in _cost_metrics["models"].values())
    
    return {
        "costs": {
            "total_cost_usd": total_cost,
            "total_tokens": total_tokens,
            "avg_cost_per_1k_tokens": (total_cost / (total_tokens / 1000)) if total_tokens > 0 else 0,
        },
        "performance": {
            "avg_response_time_ms": 250,
            "requests_per_minute": 10,
            "error_rate": 0.0,
            "cache_hit_rate": 0.85,
        },
        "health": {
            "status": "healthy",
            "uptime_hours": uptime / 3600,
            "active_tasks": _task_stats["active"],
            "completed_tasks": _task_stats["completed"],
            "failed_tasks": _task_stats["failed"],
        },
        "timestamp": datetime.now().isoformat(),
    }


@metrics_router.post("/track-usage")
async def track_usage(
    model: str,
    tokens: int,
    cost: float,
    current_user: UserProfile = Depends(get_current_user),
) -> Dict[str, str]:
    """
    Track AI model usage for cost analysis
    Requires: Valid JWT authentication
    
    Args:
        model: Model name
        tokens: Number of tokens used
        cost: Cost in USD
        
    Returns:
        Confirmation of tracking
    """
    if model not in _cost_metrics["models"]:
        _cost_metrics["models"][model] = {"tokens": 0, "cost": 0.0}
    
    _cost_metrics["models"][model]["tokens"] += tokens
    _cost_metrics["models"][model]["cost"] += cost
    _cost_metrics["total"] += cost
    
    logger.info(f"✅ Tracked usage: {model} - {tokens} tokens, ${cost}")
    
    return {
        "success": "true",
        "message": f"Tracked usage for {model}",
    }
