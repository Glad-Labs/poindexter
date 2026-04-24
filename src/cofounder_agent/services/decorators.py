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
import os
import time
from collections.abc import Callable
from typing import Any

from services.logger_config import get_logger

logger = get_logger(__name__)

# Phase H finish (GH#95): the module-level
# ``from services.site_config import site_config as _site_config`` is
# gone. That import bound a stale reference to the empty singleton
# constructed at site_config import time — when ``main.py``'s lifespan
# rebound ``services.site_config.site_config`` to a DB-loaded instance,
# every decorator helper here kept reading the empty original. The
# decorators run from import-time (db service classes decorate methods
# at module load), so we can't accept site_config via ctor injection
# the way services do.
#
# Instead the lifespan calls ``decorators.set_site_config(site_cfg)``
# explicitly once the DB-loaded instance is ready (see main.py's Phase H
# wiring). Until then the helpers fall back to env vars + sensible
# defaults — fine for the small window between import and lifespan
# startup, and tests monkeypatch the helpers directly rather than the
# binding (see tests/unit/services/test_decorators.py).
_site_config: Any = None


def set_site_config(site_config: Any) -> None:
    """Bind the SiteConfig instance the decorators should read from.

    Called by ``main.py``'s lifespan once the DB-loaded SiteConfig is
    available, and by tests that want a custom config without
    monkey-patching every helper. Passing ``None`` reverts to the
    env-fallback path used during early startup.
    """
    global _site_config
    _site_config = site_config


def _bool_from_env(env_key: str, default: bool) -> bool:
    val = os.getenv(env_key)
    if val is None:
        return default
    return val.strip().lower() in ("true", "1", "yes", "on")


def _int_from_env(env_key: str, default: int) -> int:
    val = os.getenv(env_key)
    if val is None:
        return default
    try:
        return int(val)
    except (ValueError, TypeError):
        return default


def _slow_query_threshold_ms() -> int:
    """Read SLOW_QUERY_THRESHOLD_MS from the bound site_config (env-fallback before lifespan)."""
    if _site_config is not None:
        return _site_config.get_int("slow_query_threshold_ms", 100)
    return _int_from_env("SLOW_QUERY_THRESHOLD_MS", 100)


def _log_all_queries() -> bool:
    if _site_config is not None:
        return _site_config.get_bool("log_all_queries", False)
    return _bool_from_env("LOG_ALL_QUERIES", False)


def _enable_query_monitoring() -> bool:
    if _site_config is not None:
        return _site_config.get_bool("enable_query_monitoring", True)
    return _bool_from_env("ENABLE_QUERY_MONITORING", True)


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


