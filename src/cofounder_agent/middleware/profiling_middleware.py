"""
Performance Profiling Middleware

Tracks request latency, identifies slow endpoints, and collects profile data.
Stores metrics to database for historical analysis and bottleneck detection.
"""

import logging
import time
from datetime import datetime, timezone
from typing import Any, Callable, Dict, Optional

from fastapi import Request, Response
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.types import ASGIApp

logger = logging.getLogger(__name__)


class ProfileData:
    """Request profile data structure"""

    def __init__(self, endpoint: str, method: str):
        self.endpoint = endpoint
        self.method = method
        self.start_time = time.time()
        self.status_code: Optional[int] = None
        self.duration_ms: float = 0
        self.timestamp = datetime.now(timezone.utc)
        self.is_slow = False
        self.slow_threshold_ms = 1000  # 1 second threshold for "slow"

    def complete(self, status_code: int):
        """Mark profile as complete with response status"""
        self.status_code = status_code
        self.duration_ms = (time.time() - self.start_time) * 1000
        self.is_slow = self.duration_ms > self.slow_threshold_ms

    def to_dict(self) -> Dict[str, Any]:
        """Convert profile to dictionary for storage"""
        return {
            "endpoint": self.endpoint,
            "method": self.method,
            "status_code": self.status_code,
            "duration_ms": round(self.duration_ms, 2),
            "timestamp": self.timestamp.isoformat(),
            "is_slow": self.is_slow,
        }


class ProfilingMiddleware(BaseHTTPMiddleware):
    """
    Middleware to track request latency and identify slow endpoints.

    Features:
    - Measures end-to-end request duration
    - Identifies requests exceeding threshold (1000ms)
    - Logs slow endpoints for debugging
    - Stores profiles in in-memory cache

    Usage in FastAPI:
        app.add_middleware(ProfilingMiddleware)
    """

    def __init__(self, app: ASGIApp):
        super().__init__(app)
        self.profiles: list[ProfileData] = []
        self.max_profiles = 1000  # Store last 1000 profiles
        self.slow_endpoints: Dict[str, list[ProfileData]] = {}

    async def dispatch(self, request: Request, call_next: Callable) -> Response:
        """Process request and track timing"""

        # Skip profiling for health checks and non-essential endpoints
        if request.url.path in ["/health", "/metrics"]:
            return await call_next(request)

        # Create profile
        profile = ProfileData(endpoint=request.url.path, method=request.method)

        try:
            # Process the request
            response = await call_next(request)
            profile.complete(response.status_code)

            # Store profile
            self._store_profile(profile)

            # Log slow endpoints
            if profile.is_slow:
                self._log_slow_endpoint(profile)

            return response

        except Exception as e:
            # Mark as error
            profile.complete(500)
            profile.is_slow = True
            self._store_profile(profile)
            logger.error(f"Request failed: {profile.endpoint} - {str(e)}")
            raise

    def _store_profile(self, profile: ProfileData):
        """Store profile and maintain recent history"""
        self.profiles.append(profile)

        # Keep only recent profiles
        if len(self.profiles) > self.max_profiles:
            self.profiles = self.profiles[-self.max_profiles :]

        # Track slow endpoint
        endpoint = profile.endpoint
        if profile.is_slow:
            if endpoint not in self.slow_endpoints:
                self.slow_endpoints[endpoint] = []
            self.slow_endpoints[endpoint].append(profile)

            # Limit slow endpoint history
            if len(self.slow_endpoints[endpoint]) > 100:
                self.slow_endpoints[endpoint] = self.slow_endpoints[endpoint][-100:]

    def _log_slow_endpoint(self, profile: ProfileData):
        """Log slow endpoint for monitoring"""
        logger.warning(
            f"⚠️ SLOW ENDPOINT: {profile.method} {profile.endpoint} "
            f"took {profile.duration_ms:.0f}ms (status: {profile.status_code})"
        )

    def get_recent_profiles(self, limit: int = 100) -> list[Dict[str, Any]]:
        """Get recent request profiles"""
        return [p.to_dict() for p in self.profiles[-limit:]]

    def get_slow_endpoints(self, threshold_ms: int = 1000) -> Dict[str, Any]:
        """Get endpoints that exceed threshold with average latency"""
        slow_stats = {}

        for endpoint, profiles in self.slow_endpoints.items():
            if profiles:
                durations = [p.duration_ms for p in profiles]
                avg_duration = sum(durations) / len(durations)

                if avg_duration >= threshold_ms:
                    slow_stats[endpoint] = {
                        "avg_duration_ms": round(avg_duration, 2),
                        "count": len(profiles),
                        "max_duration_ms": round(max(durations), 2),
                        "min_duration_ms": round(min(durations), 2),
                        "status_codes": list(set(p.status_code for p in profiles)),
                    }

        return slow_stats

    def get_endpoint_stats(self) -> Dict[str, Any]:
        """Get statistics for all endpoints"""
        stats_by_endpoint: Dict[str, list[ProfileData]] = {}

        for profile in self.profiles:
            if profile.endpoint not in stats_by_endpoint:
                stats_by_endpoint[profile.endpoint] = []
            stats_by_endpoint[profile.endpoint].append(profile)

        endpoint_stats = {}
        for endpoint, profiles in stats_by_endpoint.items():
            durations = [p.duration_ms for p in profiles]
            status_codes = [p.status_code for p in profiles]

            endpoint_stats[endpoint] = {
                "total_requests": len(profiles),
                "avg_duration_ms": round(sum(durations) / len(durations), 2),
                "max_duration_ms": round(max(durations), 2),
                "min_duration_ms": round(min(durations), 2),
                "p95_duration_ms": round(
                    sorted(durations)[int(len(durations) * 0.95)] if durations else 0, 2
                ),
                "p99_duration_ms": round(
                    sorted(durations)[int(len(durations) * 0.99)] if durations else 0, 2
                ),
                "error_count": sum(1 for s in status_codes if s >= 400),
                "success_rate": (
                    round(sum(1 for s in status_codes if s < 400) / len(status_codes) * 100, 2)
                    if status_codes
                    else 0
                ),
            }

        return endpoint_stats
