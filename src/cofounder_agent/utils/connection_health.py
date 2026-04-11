"""
Database connection health check utility.

Monitors database connection pool health and provides diagnostics
for stale connections and pool exhaustion.
"""

import asyncio
from datetime import datetime, timezone
from typing import Any, Dict, Optional

from services.logger_config import get_logger

logger = get_logger(__name__)


class ConnectionPoolHealth:
    """Monitor and report on connection pool health."""

    def __init__(self, pool, check_interval: int = 60):
        """
        Initialize connection pool health monitor.

        Args:
            pool: asyncpg connection pool to monitor
            check_interval: Seconds between health checks (default: 60)
        """
        self.pool = pool
        self.check_interval = check_interval
        self.last_check_time: datetime | None = None
        self.last_check_status: dict[str, Any] | None = None
        self.consecutive_failures = 0
        self.max_consecutive_failures = 3

    async def check_pool_health(self) -> dict[str, Any]:
        """
        Check the health of the connection pool.

        Returns:
            Dictionary with health status and metrics
        """
        if not self.pool:
            return {"healthy": False, "reason": "Pool not initialized"}

        try:
            # Try to acquire and release a connection
            start_time = asyncio.get_running_loop().time()
            async with asyncio.timeout(5):  # 5-second timeout for health check
                async with self.pool.acquire() as conn:
                    await conn.fetchval("SELECT 1")

            check_duration = asyncio.get_running_loop().time() - start_time

            # Get pool stats
            pool_size = self.pool.get_size()
            pool_idle = self.pool.get_idle_size()
            pool_used = pool_size - pool_idle

            status = {
                "healthy": True,
                "pool_size": pool_size,
                "pool_used": pool_used,
                "pool_idle": pool_idle,
                "check_duration_ms": round(check_duration * 1000, 2),
                "consecutive_failures": 0,
                "timestamp": datetime.now(timezone.utc).isoformat(),
            }

            self.last_check_status = status
            self.last_check_time = datetime.now(timezone.utc)
            self.consecutive_failures = 0

            logger.debug(f"✅ Pool health check passed: {pool_size} connections, {pool_idle} idle")
            return status

        except asyncio.TimeoutError:
            self.consecutive_failures += 1
            status = {
                "healthy": False,
                "reason": "Health check timeout",
                "consecutive_failures": self.consecutive_failures,
                "timestamp": datetime.now(timezone.utc).isoformat(),
            }
            logger.warning(
                f"⚠️  Pool health check timeout "
                f"(consecutive failures: {self.consecutive_failures})",
                exc_info=True,
            )
            return status

        except Exception as e:
            self.consecutive_failures += 1
            status = {
                "healthy": False,
                "reason": str(e),
                "error_type": type(e).__name__,
                "consecutive_failures": self.consecutive_failures,
                "timestamp": datetime.now(timezone.utc).isoformat(),
            }
            logger.error(
                f"❌ Pool health check failed: {e} "
                f"(consecutive failures: {self.consecutive_failures})",
                exc_info=True,
            )
            return status

    async def auto_health_check(self) -> None:
        """
        Run periodic health checks on the connection pool.

        Should be started as a background task in application startup.
        """
        logger.info(f"🏥 Starting connection pool health checks (interval: {self.check_interval}s)")

        while True:
            try:
                await asyncio.sleep(self.check_interval)
                status = await self.check_pool_health()

                if not status.get("healthy", False):
                    consecutive = status.get("consecutive_failures", 0)
                    if consecutive >= self.max_consecutive_failures:
                        logger.critical(
                            "[POOL_UNHEALTHY] Connection pool has failed %d consecutive "
                            "health checks — manual intervention may be required",
                            consecutive,
                        )
                        try:
                            import sentry_sdk

                            sentry_sdk.capture_message(
                                f"Connection pool unhealthy: {consecutive} consecutive failures",
                                level="error",
                            )
                        except Exception:
                            pass

            except asyncio.CancelledError:
                logger.info("🏥 Connection pool health checks stopped")
                break
            except Exception as e:
                logger.error(f"Error in health check loop: {e}", exc_info=True)
                await asyncio.sleep(self.check_interval)

    def get_health_summary(self) -> dict[str, Any]:
        """Get summary of last health check."""
        if self.last_check_status is None:
            return {"status": "unknown", "message": "No health checks performed yet"}

        return {
            "timestamp": self.last_check_time.isoformat() if self.last_check_time else None,
            **self.last_check_status,
        }

    def is_pool_degraded(self) -> bool:
        """
        Check if pool is degraded (not healthy but not critical).

        Returns:
            True if pool is showing signs of degradation
        """
        if self.last_check_status is None:
            return False

        # Consider degraded if:
        # 1. Many consecutive failures
        if self.consecutive_failures >= 2:
            return True

        # 2. High utilization
        pool_used = self.last_check_status.get("pool_used", 0)
        pool_size = self.last_check_status.get("pool_size", 1)
        utilization = pool_used / pool_size if pool_size > 0 else 0

        if utilization > 0.8:  # >80% utilization
            logger.warning(f"⚠️  Pool utilization high: {utilization:.0%}")
            return True

        return False

    def is_pool_critical(self) -> bool:
        """
        Check if pool is in critical state.

        Returns:
            True if pool needs immediate attention
        """
        if self.last_check_status is None:
            return False

        # Critical if:
        # 1. Multiple consecutive failures
        if self.consecutive_failures >= self.max_consecutive_failures:
            return True

        # 2. All connections exhausted
        pool_idle = self.last_check_status.get("pool_idle", 1)
        if pool_idle == 0:
            logger.error("🚨 Connection pool exhausted!")
            return True

        return False


async def diagnose_connection_issues() -> dict[str, Any]:
    """
    Run diagnostic checks for common connection issues.

    Returns:
        Dictionary with diagnostic information
    """
    diagnostics = {
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "issues": [],
        "recommendations": [],
    }

    # Check if database URL is configured
    import os

    db_url = os.getenv("DATABASE_URL")
    if not db_url:
        diagnostics["issues"].append("DATABASE_URL environment variable not set")
        diagnostics["recommendations"].append("Set DATABASE_URL environment variable")

    # Check pool configuration — defaults must match database_service.py
    from config import get_config

    _config = get_config()
    _is_dev = _config.environment.lower() in ("development", "dev", "local")
    from services.site_config import site_config
    pool_min = site_config.get("database_pool_min_size", "5" if _is_dev else "20")
    pool_max = site_config.get("database_pool_max_size", "20" if _is_dev else "50")

    try:
        pool_min_val = int(pool_min)
        pool_max_val = int(pool_max)

        if pool_min_val > pool_max_val:
            diagnostics["issues"].append(
                f"Invalid pool config: min ({pool_min_val}) > max ({pool_max_val})"
            )
            diagnostics["recommendations"].append(
                "Set DATABASE_POOL_MIN_SIZE <= DATABASE_POOL_MAX_SIZE"
            )

        if pool_max_val > 100:
            diagnostics["recommendations"].append(
                f"High pool max size ({pool_max_val}) - may cause resource issues"
            )

    except ValueError:
        diagnostics["issues"].append("Invalid DATABASE_POOL_* environment variables")

    return diagnostics
