"""
Metrics and Analytics Routes
Provides endpoints for tracking AI model usage, costs, and performance metrics

All endpoints require JWT authentication

Integrates with UsageTracker service for real-time metrics collection.
"""

from fastapi import APIRouter, HTTPException, Depends, Query
from pydantic import BaseModel, Field
from typing import Dict, Any, List, Optional
from datetime import datetime
import logging

from routes.auth_unified import get_current_user, UserProfile
from services.usage_tracker import get_usage_tracker

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


@metrics_router.get("/usage", response_model=Dict[str, Any])
async def get_usage_metrics(
    current_user: UserProfile = Depends(get_current_user),
    period: str = Query("last_24h", description="Time period: last_1h, last_24h, last_7d, all")
) -> Dict[str, Any]:
    """
    Get comprehensive usage metrics from UsageTracker.
    
    **Authentication:** Requires valid JWT token
    
    **Parameters:**
    - period: Time period to aggregate (last_1h, last_24h, last_7d, all)
    
    **Returns:**
    - Token usage (input/output breakdown)
    - Cost analysis by model and operation type
    - Success/failure rates
    - Performance metrics (duration, throughput)
    """
    try:
        tracker = get_usage_tracker()
        completed_ops = tracker.completed_operations
        
        if not completed_ops:
            return {
                "timestamp": datetime.utcnow().isoformat(),
                "period": period,
                "total_operations": 0,
                "tokens": {
                    "total": 0,
                    "input": 0,
                    "output": 0,
                    "avg_per_operation": 0.0
                },
                "costs": {
                    "total": 0.0,
                    "avg_per_operation": 0.0,
                    "by_model": {},
                    "projected_monthly": 0.0
                },
                "operations": {
                    "total": 0,
                    "successful": 0,
                    "failed": 0,
                    "success_rate": 0.0
                },
                "by_model": {},
                "by_operation_type": {}
            }
        
        # Calculate metrics
        total_input = sum(op.get("tokens_in", 0) for op in completed_ops)
        total_output = sum(op.get("tokens_out", 0) for op in completed_ops)
        total_tokens = total_input + total_output
        total_cost = sum(op.get("cost_estimate", 0.0) for op in completed_ops)
        total_ops = len(completed_ops)
        successful_ops = sum(1 for op in completed_ops if op.get("success", False))
        failed_ops = total_ops - successful_ops
        
        # Group by model
        by_model = {}
        for op in completed_ops:
            model = op.get("model", "unknown")
            if model not in by_model:
                by_model[model] = {"operations": 0, "tokens": 0, "cost": 0.0}
            by_model[model]["operations"] += 1
            by_model[model]["tokens"] += op.get("tokens_in", 0) + op.get("tokens_out", 0)
            by_model[model]["cost"] += op.get("cost_estimate", 0.0)
        
        # Group by operation type
        by_operation = {}
        for op in completed_ops:
            op_type = op.get("operation_type", "unknown")
            if op_type not in by_operation:
                by_operation[op_type] = {"count": 0, "cost": 0.0, "success": 0}
            by_operation[op_type]["count"] += 1
            by_operation[op_type]["cost"] += op.get("cost_estimate", 0.0)
            if op.get("success", False):
                by_operation[op_type]["success"] += 1
        
        # Projections
        days_active = max(1, (datetime.utcnow() - datetime.fromisoformat(
            completed_ops[0].get("started_at", datetime.utcnow().isoformat())
        )).days or 1)
        projected_monthly = (total_cost / days_active * 30) if days_active > 0 else 0
        
        return {
            "timestamp": datetime.utcnow().isoformat(),
            "period": period,
            "total_operations": total_ops,
            "tokens": {
                "total": int(total_tokens),
                "input": int(total_input),
                "output": int(total_output),
                "avg_per_operation": float(total_tokens / total_ops) if total_ops > 0 else 0.0
            },
            "costs": {
                "total": round(total_cost, 4),
                "avg_per_operation": round(total_cost / total_ops, 6) if total_ops > 0 else 0.0,
                "by_model": {model: round(by_model[model]["cost"], 4) for model in by_model},
                "projected_monthly": round(projected_monthly, 2)
            },
            "operations": {
                "total": total_ops,
                "successful": successful_ops,
                "failed": failed_ops,
                "success_rate": round((successful_ops / total_ops * 100) if total_ops > 0 else 0, 2)
            },
            "by_model": by_model,
            "by_operation_type": by_operation
        }
    
    except Exception as e:
        logger.error(f"Error retrieving usage metrics: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=f"Failed to retrieve metrics: {str(e)}")


@metrics_router.get("/costs")
async def get_cost_metrics(
    current_user: UserProfile = Depends(get_current_user)
) -> Dict[str, Any]:
    """
    Get AI model usage and cost metrics (backward compatible endpoint).
    
    **Authentication:** Requires valid JWT token
    
    **Returns:**
    - Cost breakdown by model and provider
    - Token usage statistics
    - Cost trends and projections
    """
    try:
        tracker = get_usage_tracker()
        completed_ops = tracker.completed_operations
        
        if not completed_ops:
            return {
                "total_cost": 0.0,
                "total_tokens": 0,
                "by_model": [],
                "by_provider": {},
                "period": "all_time",
                "updated_at": datetime.now().isoformat(),
            }
        
        # Calculate totals
        total_cost = sum(op.get("cost_estimate", 0.0) for op in completed_ops)
        total_tokens = sum(op.get("tokens_in", 0) + op.get("tokens_out", 0) for op in completed_ops)
        
        # Group by model
        by_model = {}
        for op in completed_ops:
            model = op.get("model", "unknown")
            if model not in by_model:
                by_model[model] = {"tokens": 0, "cost": 0.0, "provider": "unknown"}
            by_model[model]["tokens"] += op.get("tokens_in", 0) + op.get("tokens_out", 0)
            by_model[model]["cost"] += op.get("cost_estimate", 0.0)
            
            # Infer provider from model name
            if "ollama" in model.lower() or model == "mistral" or model == "llama2":
                by_model[model]["provider"] = "ollama"
            elif "gpt" in model.lower():
                by_model[model]["provider"] = "openai"
            elif "claude" in model.lower():
                by_model[model]["provider"] = "anthropic"
        
        by_model_list = [
            {
                "model": name,
                "tokens": metrics["tokens"],
                "cost": round(metrics["cost"], 4),
                "provider": metrics["provider"],
            }
            for name, metrics in by_model.items()
        ]
        
        # Group by provider
        by_provider = {}
        for model_data in by_model_list:
            provider = model_data["provider"]
            if provider not in by_provider:
                by_provider[provider] = 0.0
            by_provider[provider] += model_data["cost"]
        
        return {
            "total_cost": round(total_cost, 4),
            "total_tokens": int(total_tokens),
            "by_model": by_model_list,
            "by_provider": {provider: round(cost, 4) for provider, cost in by_provider.items()},
            "period": "all_time",
            "updated_at": datetime.now().isoformat(),
        }
    
    except Exception as e:
        logger.error(f"Error retrieving cost metrics: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=f"Failed to retrieve metrics: {str(e)}")


@metrics_router.get("")
async def get_metrics(
    current_user: UserProfile = Depends(get_current_user)
) -> Dict[str, Any]:
    """
    Get aggregated application metrics and health status.
    
    **Authentication:** Requires valid JWT token
    
    **Returns:**
    - System health and status
    - Active and completed operations
    - API version and service status
    """
    try:
        tracker = get_usage_tracker()
        completed_ops = tracker.completed_operations
        active_ops = len(tracker.active_operations)
        
        # Calculate uptime
        uptime = (datetime.now() - _start_time).total_seconds()
        failed_ops = sum(1 for op in completed_ops if not op.get("success", False))
        
        return {
            "status": "healthy",
            "uptime_seconds": uptime,
            "active_tasks": active_ops,
            "completed_tasks": len(completed_ops),
            "failed_tasks": failed_ops,
            "api_version": "2.0.0",
            "timestamp": datetime.now().isoformat(),
            "services": {
                "database": "healthy",
                "ollama": "healthy",
                "cache": "healthy",
                "usage_tracker": "healthy"
            },
            "latest_operations": [
                {
                    "id": op.get("operation_id"),
                    "type": op.get("operation_type"),
                    "model": op.get("model"),
                    "success": op.get("success"),
                    "timestamp": op.get("completed_at", op.get("started_at"))
                }
                for op in completed_ops[-5:]  # Last 5 operations
            ]
        }
    
    except Exception as e:
        logger.error(f"Error retrieving metrics: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=f"Failed to retrieve metrics: {str(e)}")


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
