"""
Performance Monitoring Decorators

Provides decorators for tracking performance metrics across database operations,
API calls, and agent executions.

DI seam (SiteConfig constructor-DI migration PR 6, 2026-05-28
— ``docs/architecture/2026-05-28-site-config-di-migration.md``):

This module has been migrated to the constructor-DI pattern via the
``Decorators`` class. ``AppContainer.decorators`` constructs an instance
once per entry-point bootstrap and pins it as the module-level
``_default_decorators`` facade so the existing ``@log_query_performance(...)``
call-site ergonomics keep working.

**Why Option B (facade) and not Option A (pure DI)**: there are ~50
``@log_query_performance(...)`` decoration sites across ``services/*_db.py``
modules (``content_db`` / ``admin_db`` / ``tasks_db`` / ``writing_style_db``
/ ``users_db``). Forcing every site to switch from
``@log_query_performance(...)`` to ``@container.decorators.log_query_performance(...)``
would touch every DB-layer class — a huge mechanical change for no
behaviour win during the migration. A thin module-level facade that
delegates to ``_default_decorators`` keeps every call site
zero-change; the container still owns construction; tests still get a
clean injection seam via ``set_default_decorators``.

A future refactor pass can move to pure DI once Module v1 work pulls
the DB-layer modules into their owning Module.

Settings consumed (all from ``site_config``):
    slow_query_threshold_ms: Milliseconds to consider a query slow (default: 100)
    log_all_queries: Log all queries regardless of performance (default: false)
    enable_query_monitoring: Enable/disable query monitoring (default: true)

Usage:
    from services.decorators import log_query_performance

    @log_query_performance(operation="get_tasks", category="task_retrieval")
    async def get_tasks_paginated(self, offset, limit):
        # Implementation
        pass
"""

import functools
import time
from collections.abc import Callable, Coroutine
from typing import Any, ParamSpec, TypeVar

from services.logger_config import get_logger
from services.site_config import SiteConfig

logger = get_logger(__name__)

_P = ParamSpec("_P")
_T = TypeVar("_T")


class Decorators:
    """Performance-monitoring decorator factory with constructor DI.

    Holds the ``SiteConfig`` reference used by the logging branches in
    ``log_query_performance``. Constructed once per entry-point bootstrap
    by ``AppContainer.decorators`` and pinned as the module-level
    ``_default_decorators`` so the bare ``log_query_performance`` import
    keeps working at every call site.

    Per the SiteConfig DI migration design (fail-loud): the constructor
    raises ``TypeError`` when ``site_config`` is missing. No silent
    fallback to an env-var default.
    """

    def __init__(self, *, site_config: SiteConfig) -> None:
        if site_config is None:
            raise TypeError(
                "Decorators(site_config=...) requires a SiteConfig instance"
            )
        self._site_config = site_config

    # ------------------------------------------------------------------
    # Site-config reads — kept as methods so the per-call lookup path
    # mirrors the pre-migration ``_slow_query_threshold_ms()`` module
    # helpers (the values are NOT captured at decoration time — they're
    # read every time the wrapper runs, so a settings update flows
    # through without a worker restart).
    # ------------------------------------------------------------------

    def _slow_query_threshold_ms(self) -> int:
        """Read slow_query_threshold_ms from site_config per-call."""
        return self._site_config.get_int("slow_query_threshold_ms", 100)

    def _log_all_queries(self) -> bool:
        return self._site_config.get_bool("log_all_queries", False)

    def _enable_query_monitoring(self) -> bool:
        return self._site_config.get_bool("enable_query_monitoring", True)

    def log_query_performance(
        self,
        operation: str,
        category: str = "database",
        slow_threshold_ms: int | None = None,
    ) -> Callable[[Callable[_P, Coroutine[Any, Any, _T]]], Callable[_P, Coroutine[Any, Any, _T]]]:
        """Method form — bound to this instance's SiteConfig."""
        return _build_log_query_performance(
            decorators=self,
            operation=operation,
            category=category,
            slow_threshold_ms=slow_threshold_ms,
        )


# ---------------------------------------------------------------------------
# Module-level facade — kept zero-churn for the ~50 ``@log_query_performance``
# decoration sites under ``services/*_db.py``.
#
# ``_default_decorators`` is set by ``AppContainer.decorators`` during
# container construction (see ``services/container.py``). It can also be
# overridden by tests via ``set_default_decorators(Decorators(site_config=...))``.
#
# Until set, ``_ensure_default()`` lazily constructs a fallback instance
# with a fresh empty ``SiteConfig`` — same behaviour as pre-migration
# ``site_config: SiteConfig = SiteConfig()``. This keeps imports cheap and
# avoids crashing tests that import this module before any container or
# fixture has run. Production entry points (worker lifespan, Prefect
# subprocess, brain daemon, CLI) all construct an ``AppContainer``, which
# replaces the fallback with the DB-loaded instance before any decorated
# function runs.
# ---------------------------------------------------------------------------

_default_decorators: Decorators | None = None


def _ensure_default() -> Decorators:
    """Return the module-level Decorators instance, lazily constructing
    a fallback if no AppContainer or test has set one yet."""
    global _default_decorators
    if _default_decorators is None:
        _default_decorators = Decorators(site_config=SiteConfig())
    return _default_decorators


def set_default_decorators(decorators: Decorators | None) -> None:
    """Pin (or unpin) the module-level ``Decorators`` facade.

    Called by ``AppContainer.decorators`` at container construction so
    the module-level ``log_query_performance`` decorator routes through
    the container-wired ``SiteConfig`` instance. Tests can also call
    this directly to swap in a stub-config-backed instance, then call
    ``set_default_decorators(None)`` in teardown to reset.
    """
    global _default_decorators
    _default_decorators = decorators


# ---------------------------------------------------------------------------
# Test/back-compat shims for the module-level reads. These are the same
# names the pre-migration code exposed (``_slow_query_threshold_ms`` etc.);
# the existing unit tests monkeypatch them directly, so keeping the names
# means the tests keep working without churn. Each one delegates to the
# current ``_default_decorators``.
# ---------------------------------------------------------------------------


def _slow_query_threshold_ms() -> int:
    return _ensure_default()._slow_query_threshold_ms()


def _log_all_queries() -> bool:
    return _ensure_default()._log_all_queries()


def _enable_query_monitoring() -> bool:
    return _ensure_default()._enable_query_monitoring()


def _build_log_query_performance(
    *,
    decorators: Decorators | None,
    operation: str,
    category: str,
    slow_threshold_ms: int | None,
) -> Callable[[Callable[_P, Coroutine[Any, Any, _T]]], Callable[_P, Coroutine[Any, Any, _T]]]:
    """Construct the actual decorator.

    ``decorators`` is captured at decoration time and used as the source
    of the per-call site_config reads inside the wrapper. When ``None``
    (the module-level facade path), the wrapper falls through to the
    module-level helpers — those re-resolve ``_default_decorators`` on
    every call, so a late container construction still propagates.

    The module-level ``_slow_query_threshold_ms`` / ``_log_all_queries`` /
    ``_enable_query_monitoring`` helpers are kept around explicitly so
    the existing unit tests' ``monkeypatch.setattr("services.decorators.
    _enable_query_monitoring", ...)`` calls keep targeting the same
    surface.
    """

    def _read_enable_query_monitoring() -> bool:
        if decorators is None:
            return _enable_query_monitoring()
        return decorators._enable_query_monitoring()

    def _read_slow_query_threshold_ms() -> int:
        if decorators is None:
            return _slow_query_threshold_ms()
        return decorators._slow_query_threshold_ms()

    def _read_log_all_queries() -> bool:
        if decorators is None:
            return _log_all_queries()
        return decorators._log_all_queries()

    def decorator(
        func: Callable[_P, Coroutine[Any, Any, _T]]
    ) -> Callable[_P, Coroutine[Any, Any, _T]]:
        @functools.wraps(func)
        async def wrapper(*args: _P.args, **kwargs: _P.kwargs) -> _T:
            # Skip if monitoring disabled
            if not _read_enable_query_monitoring():
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
                    slow_threshold_ms
                    if slow_threshold_ms is not None
                    else _read_slow_query_threshold_ms()
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
                elif _read_log_all_queries():
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


def log_query_performance(
    operation: str,
    category: str = "database",
    slow_threshold_ms: int | None = None,
) -> Callable[[Callable[_P, Coroutine[Any, Any, _T]]], Callable[_P, Coroutine[Any, Any, _T]]]:
    """
    Decorator to log query performance metrics.

    Module-level facade — delegates to ``_default_decorators`` (the
    container-wired ``Decorators`` instance). Passing ``decorators=None``
    means the wrapper reads through the late-bound module-level helpers
    on every call, so a container construction that happens AFTER class
    decoration (which is the normal order — modules import first, then
    the lifespan builds the container) propagates fresh settings without
    a re-decoration.

    Captures execution time, logs slow queries, and includes context about the operation.

    Args:
        operation: Name of the database operation (e.g., "get_tasks", "list_content")
        category: Category of operation for grouping (e.g., "task_retrieval", "content")
        slow_threshold_ms: Override threshold for slow query warning (default: from site_config)

    Returns:
        Decorator function that wraps the target async function

    Example:
        @log_query_performance(operation="get_tasks_paginated", category="task_retrieval")
        async def get_tasks_paginated(self, offset, limit):
            # Database query here
            return results
    """
    return _build_log_query_performance(
        decorators=None,
        operation=operation,
        category=category,
        slow_threshold_ms=slow_threshold_ms,
    )
