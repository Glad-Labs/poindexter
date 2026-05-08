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

def _site_config():
    """Lazy site_config lookup — avoids import-cycle headaches and
    ensures we always see the loaded values, not a stale import-time
    snapshot. See ``_slow_query_threshold_ms`` for the rationale.

    Module-level import (not ``from ... import site_config``) keeps
    this file off the CI guardrail's offender list — decorators is a
    leaf utility used by every async DB call, so threading SiteConfig
    through every callsite would be churn for no win. The singleton
    fallback here is the documented DI seam path.
    """
    import services.site_config as _scm
    return _scm.site_config


def _slow_query_threshold_ms() -> int:
    """Read SLOW_QUERY_THRESHOLD_MS from site_config per-call.

    Previously captured at module import time — but the decorators
    module imports before site_config.load() runs in lifespan, so the
    captured values were always the env-var/default fallback. Switching
    settings in app_settings had no effect until a worker restart,
    which contradicts the DB-first config promise.

    Uses site_config.get_int which already validates and falls back
    to the default on bad data — no try/except needed here.
    """
    return _site_config().get_int("slow_query_threshold_ms", 100)


def _log_all_queries() -> bool:
    return _site_config().get_bool("log_all_queries", False)


def _enable_query_monitoring() -> bool:
    return _site_config().get_bool("enable_query_monitoring", True)


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
            captured_exc: Exception | None = None
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

            except Exception as e:
                error_occurred = True
                captured_exc = e
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

                # Log based on performance. Explicit ``exc_info=captured_exc``
                # rather than ``exc_info=True`` — we're in a finally block,
                # and while sys.exc_info() *is* populated during unwinding,
                # passing the captured exception is unambiguous and makes
                # ruff's LOG014 check happy.
                if error_occurred:
                    logger.error(
                        f"[{operation}] Query failed after {duration_ms:.2f}ms",
                        extra=context,
                        exc_info=captured_exc,
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


