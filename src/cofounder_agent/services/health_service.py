"""
Health Service for Glad Labs AI Co-Founder

This module provides centralized health check functionality.
"""

import asyncio
import logging
from datetime import datetime, timezone
from typing import Any, Dict, Optional

logger = logging.getLogger(__name__)

from fastapi import FastAPI

# Import configuration
from config import get_config

# Get configuration
config = get_config()


class HealthService:
    """Centralized health check service."""

    def __init__(self, app: FastAPI):
        self.app = app
        self._startup_error: Optional[str] = None
        self._startup_complete: bool = False

    def set_startup_status(self, error: Optional[str] = None, complete: bool = False) -> None:
        """Set startup status."""
        self._startup_error = error
        self._startup_complete = complete

    async def check_health(self) -> Dict[str, Any]:
        """Perform comprehensive health check."""
        health_data = {
            "status": "healthy",
            "service": "cofounder-agent",
            "version": config.app_version,
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "components": {},
        }

        # Check startup status
        if self._startup_error:
            health_data["status"] = "degraded"
            health_data["startup_error"] = self._startup_error
            health_data["startup_complete"] = self._startup_complete
        elif not self._startup_complete:
            health_data["status"] = "starting"
            health_data["startup_complete"] = False

        # Include database status if available
        database_service = getattr(self.app.state, "database", None)
        if database_service:
            try:
                db_health = await database_service.health_check()
                health_data["components"]["database"] = db_health.get("status", "unknown")
            except Exception as e:  # pylint: disable=broad-except
                # Log the error but don't fail the health check
                logger.error("[health_service] Database health check failed", exc_info=True)
                health_data["components"]["database"] = "degraded"
        else:
            health_data["components"]["database"] = "unavailable"

        # Include Redis cache status if available
        redis_cache = getattr(self.app.state, "redis_cache", None)
        if redis_cache:
            try:
                redis_health = await redis_cache.health_check()
                health_data["components"]["redis"] = redis_health.get("status", "unknown")
            except Exception as e:  # pylint: disable=broad-except
                logger.error("[health_service] Redis health check failed", exc_info=True)
                health_data["components"]["redis"] = "degraded"
        else:
            health_data["components"]["redis"] = "unavailable"

        return health_data


# Global health service instance
health_service = None


def get_health_service(app: FastAPI) -> HealthService:
    """Get or create the global health service instance."""
    global health_service
    if health_service is None:
        health_service = HealthService(app)
    return health_service
