"""
Profiling Routes

API endpoints for accessing performance profile data collected by ProfilingMiddleware.
Helps identify slow endpoints and performance bottlenecks.
"""

import logging

from fastapi import APIRouter, Depends, HTTPException, Query
from routes.auth_unified import get_current_user

logger = logging.getLogger(__name__)

# Will be set by main.py during app initialization
profiling_middleware = None


def init_profiling_routes(app, middleware_instance):
    """Initialize profiling routes with middleware reference"""
    global profiling_middleware
    profiling_middleware = middleware_instance


router = APIRouter(
    prefix="/api/profiling",
    tags=["profiling"],
    dependencies=[Depends(get_current_user)],
)


@router.get("/slow-endpoints")
async def get_slow_endpoints(threshold_ms: int = Query(1000, ge=0)):
    """
    Get endpoints that exceed latency threshold.

    Query Parameters:
    - threshold_ms: Latency threshold in milliseconds (default: 1000)

    Returns:
    {
        "/api/tasks": {
            "avg_duration_ms": 1250.5,
            "count": 15,
            "max_duration_ms": 2100.0,
            "min_duration_ms": 900.0,
            "status_codes": [200, 201]
        },
        ...
    }
    """
    if profiling_middleware is None:
        raise HTTPException(status_code=503, detail="Profiling middleware not initialized")

    slow_endpoints = profiling_middleware.get_slow_endpoints(threshold_ms)

    return {
        "threshold_ms": threshold_ms,
        "slow_endpoints": slow_endpoints,
        "count": len(slow_endpoints),
    }


@router.get("/endpoint-stats")
async def get_endpoint_stats():
    """
    Get comprehensive statistics for all endpoints.

    Returns statistics including:
    - total_requests: Total requests to endpoint
    - avg_duration_ms: Average request duration
    - max_duration_ms: Slowest request
    - min_duration_ms: Fastest request
    - p95_duration_ms: 95th percentile duration
    - p99_duration_ms: 99th percentile duration
    - error_count: Number of failed requests
    - success_rate: Percentage of successful requests
    """
    if profiling_middleware is None:
        raise HTTPException(status_code=503, detail="Profiling middleware not initialized")

    stats = profiling_middleware.get_endpoint_stats()

    return {
        "timestamp": str(__import__("datetime").datetime.now(__import__("datetime").timezone.utc)),
        "endpoint_count": len(stats),
        "endpoints": stats,
    }


@router.get("/recent-requests")
async def get_recent_requests(limit: int = Query(100, ge=1, le=1000)):
    """
    Get recent request profiles.

    Query Parameters:
    - limit: Number of recent requests to return (default: 100, max: 1000)

    Returns list of recent profiles with timing data.
    """
    if profiling_middleware is None:
        raise HTTPException(status_code=503, detail="Profiling middleware not initialized")

    profiles = profiling_middleware.get_recent_profiles(limit)

    return {
        "limit": limit,
        "count": len(profiles),
        "profiles": profiles,
    }


@router.get("/phase-breakdown")
async def get_phase_breakdown():
    """
    Get breakdown of task execution by phase.
    Analyzes request patterns to /tasks endpoints and their phases.

    Returns performance metrics grouped by:
    - Phase (generation, quality_assessment, publishing, etc.)
    - Average duration per phase
    - Slow phase detection
    """
    if profiling_middleware is None:
        raise HTTPException(status_code=503, detail="Profiling middleware not initialized")

    # Analyze task-related endpoints
    stats = profiling_middleware.get_endpoint_stats()
    phase_stats = {}

    task_endpoints = {k: v for k, v in stats.items() if "/tasks" in k or "/api/tasks" in k}

    for endpoint, data in task_endpoints.items():
        # Extract phase info from logging context if available
        # For now, aggregate by endpoint as proxy for phase
        duration = data.get("avg_duration_ms", 0)
        phase_name = endpoint.replace("/api/tasks/", "").split("?")[0] or "overall"

        if phase_name not in phase_stats:
            phase_stats[phase_name] = {
                "avg_duration_ms": 0,
                "endpoints": [],
                "total_requests": 0,
            }

        phase_stats[phase_name]["endpoints"].append(endpoint)
        phase_stats[phase_name]["avg_duration_ms"] = duration
        phase_stats[phase_name]["total_requests"] = data.get("total_requests", 0)

    # Identify slow phases
    slow_phases = {k: v for k, v in phase_stats.items() if v.get("avg_duration_ms", 0) > 1000}

    return {
        "phase_breakdown": phase_stats,
        "slow_phases": slow_phases,
        "slow_phase_count": len(slow_phases),
    }


@router.get("/health")
async def profiling_health():
    """Health check for profiling system"""
    if profiling_middleware is None:
        return {"status": "not_initialized"}

    profile_count = len(profiling_middleware.profiles)
    slow_endpoint_count = len(profiling_middleware.slow_endpoints)

    return {
        "status": "healthy",
        "profiles_tracked": profile_count,
        "slow_endpoints_detected": slow_endpoint_count,
        "max_profiles": profiling_middleware.max_profiles,
    }
