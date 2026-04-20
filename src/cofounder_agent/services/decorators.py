"""
Performance Monitoring Decorators

Provides decorators for tracking performance metrics across database operations,
API calls, and agent executions.

Environment Variables:
    SLOW_QUERY_THRESHOLD_MS: Milliseconds to consider a query slow (default: 100ms)
    LOG_ALL_QUERIES: Log all queries regardless of performance (default: false)
    ENABLE_QUERY_MONITORING: Enable/disable query monitoring (default: true)

Usage:
    from services.decorators import log_query_performance

    @log_query_performance(operation="get_tasks", category="task_retrieval")
    async def get_tasks_paginated(self, offset, limit):
        # Implementation
        pass
"""

import functools
import time
from collections.abc import Callable
from typing import Any

from services.logger_config import get_logger

logger = get_logger(__name__)

def _cfg(key: str, default: str) -> str:
    """Read from site_config (falls back to env vars automatically)."""
    from services.site_config import site_config
    return site_config.get(key, default)


def _slow_query_threshold_ms() -> int:
    """Read SLOW_QUERY_THRESHOLD_MS from site_config per-call.

    Previously captured at module import time — but the decorators
    module imports before site_config.load() runs in lifespan, so the
    captured values were always the env-var/default fallback. Switching
    settings in app_settings had no effect until a worker restart,
    which contradicts the DB-first config promise.
    """
    try:
        return int(_cfg("slow_query_threshold_ms", "100"))
    except (ValueError, TypeError):
        return 100


def _log_all_queries() -> bool:
    return _cfg("log_all_queries", "false").lower() == "true"


def _enable_query_monitoring() -> bool:
    return _cfg("enable_query_monitoring", "true").lower() == "true"


def log_query_performance(
    operation: str,
    category: str = "database",
    slow_threshold_ms: int | None = None,
):
    """
    Decorator to log query performance metrics.

    Captures execution time, logs slow queries, and includes context about the operation.

    Args:
        operation: Name of the database operation (e.g., "get_tasks", "list_content")
        category: Category of operation for grouping (e.g., "task_retrieval", "content")
        slow_threshold_ms: Override threshold for slow query warning (default: from env)

    Returns:
        Decorator function that wraps the target async function

    Example:
        @log_query_performance(operation="get_tasks_paginated", category="task_retrieval")
        async def get_tasks_paginated(self, offset, limit):
            # Database query here
            return results
    """

    def decorator(func: Callable) -> Callable:
        @functools.wraps(func)
        async def wrapper(*args, **kwargs) -> Any:
            # Skip if monitoring disabled
            if not _enable_query_monitoring():
                return await func(*args, **kwargs)

            # Start timing
            start_time = time.perf_counter()
            error_occurred = False
            result = None
            result_count = None

            try:
                result = await func(*args, **kwargs)

                # Try to determine result count
                if result is not None:
                    if isinstance(result, list):
                        result_count = len(result)
                    elif isinstance(result, dict):
                        if "results" in result and isinstance(result["results"], list):
                            result_count = len(result["results"])
                        elif "total" in result:
                            result_count = result["total"]

                return result

            except Exception:
                error_occurred = True
                raise

            finally:
                # Calculate duration
                end_time = time.perf_counter()
                duration_ms = (end_time - start_time) * 1000

                # Determine if this is a slow query
                threshold = (
                    slow_threshold_ms if slow_threshold_ms is not None else _slow_query_threshold_ms()
                )
                is_slow = duration_ms > threshold

                # Build context
                context = {
                    "operation": operation,
                    "category": category,
                    "duration_ms": round(duration_ms, 2),
                    "slow": is_slow,
                    "error": error_occurred,
                }

                # Add result count if available
                if result_count is not None:
                    context["result_count"] = result_count

                # Add function arguments (sanitize sensitive data)
                if kwargs:
                    # Filter out sensitive fields
                    safe_kwargs = {
                        k: v
                        for k, v in kwargs.items()
                        if k not in ["password", "token", "secret", "api_key"]
                    }
                    if safe_kwargs:
                        context["params"] = safe_kwargs

                # Log based on performance
                if error_occurred:
                    logger.error(
                        f"[{operation}] Query failed after {duration_ms:.2f}ms",
                        extra=context,
                        exc_info=True,
                    )
                elif is_slow:
                    logger.warning(
                        f"[{operation}] ⚠️  SLOW QUERY: {duration_ms:.2f}ms (threshold: {threshold}ms)",
                        extra=context,
                    )
                elif _log_all_queries():
                    logger.info(
                        f"[{operation}] Query completed in {duration_ms:.2f}ms", extra=context
                    )
                else:
                    # Log at debug level for fast queries when not logging all
                    logger.debug(
                        f"[{operation}] Query completed in {duration_ms:.2f}ms", extra=context
                    )

        return wrapper

    return decorator


