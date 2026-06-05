"""``KernelPlatform`` — the concrete ``Platform`` backing the running kernel.

Wave 1 of Seam 1 (Glad-Labs/poindexter#667). Adapts the live kernel services
(``SiteConfig``, the asyncpg pool, the LLM dispatch router, the ``audit_log``
writer, logging + metrics) to the capability interfaces declared in
``plugins/platform.py``.

The services are **injected** (constructor args), not imported at module level,
so this stays import-cheap and unit-testable without the worker's heavy deps —
own the interface, rent the implementation. ``main.py``'s lifespan constructs
the single ``KernelPlatform`` from the already-loaded services (Wave 2); each
module then receives a ``plugins.platform.ScopedPlatform`` wrapping it, narrowed
to that module's declared capabilities.

``dispatch`` and ``audit`` are forwarded transparently in this v1 (the module
passes the args the underlying callables expect). A cleaner, fully-typed
dispatch/audit surface is a later refinement — the goal here is to route module
access *through the handle*, not to redesign those services.
"""

from __future__ import annotations

import asyncio
import functools
import logging
from typing import Any, Awaitable, Callable, Coroutine

from plugins.platform import (
    AuditCapability,
    ConfigCapability,
    DbCapability,
    DispatchCapability,
    LogCapability,
    MetricCapability,
    SecretCapability,
)

_DEFAULT_LOGGER = logging.getLogger("poindexter.platform")

# Severities whose audit row IS the signal a downstream alert reads (#303): a
# dropped warn/critical row silently kills the alert, so a dropped *background*
# write of one escalates to error-level (Sentry-visible) instead of debug.
_LOUD_BG_SEVERITIES = frozenset({"warn", "warning", "critical"})


class _ConfigAdapter:
    """Maps the ``config`` capability onto ``SiteConfig.get`` (sync, cached)."""

    def __init__(self, site_config: Any) -> None:
        self._site_config = site_config

    def get(self, key: str, default: Any = None) -> Any:
        return self._site_config.get(key, default)


class _SecretAdapter:
    """Maps the ``secret`` capability onto ``SiteConfig.get_secret`` (async)."""

    def __init__(self, site_config: Any) -> None:
        self._site_config = site_config

    async def get(self, key: str, default: str | None = None) -> str | None:
        return await self._site_config.get_secret(key, default)


class _DispatchAdapter:
    """Forwards the ``dispatch`` capability to the LLM router callable."""

    def __init__(self, dispatch: Callable[..., Awaitable[Any]]) -> None:
        self._dispatch = dispatch

    async def complete(self, *args: Any, **kwargs: Any) -> Any:
        return await self._dispatch(*args, **kwargs)


class _DbAdapter:
    """Maps the ``db`` capability onto the asyncpg pool's ``acquire``."""

    def __init__(self, pool: Any) -> None:
        self._pool = pool

    def acquire(self) -> Any:
        # ``asyncpg.Pool.acquire()`` returns an async context manager yielding
        # a connection. (OperatorScope-scoping is layered here when #60 lands.)
        return self._pool.acquire()


class _LogAdapter:
    """Maps the ``log`` capability onto a structured logger."""

    def __init__(self, logger: logging.Logger) -> None:
        self._logger = logger

    def __call__(self, message: str, /, **fields: Any) -> None:
        # Nest structured fields under one key so they never clash with the
        # reserved ``LogRecord`` attributes.
        extra = {"platform_fields": fields} if fields else {}
        self._logger.info(message, extra=extra)


class _MetricAdapter:
    """Forwards the ``metric`` capability to the injected metric emitter."""

    def __init__(self, metric_emit: Callable[..., None]) -> None:
        self._metric_emit = metric_emit

    def __call__(self, name: str, value: float = 1.0, /, **labels: str) -> None:
        self._metric_emit(name, value, **labels)


class _AuditAdapter:
    """Forwards the ``audit`` capability to the injected ``audit_log`` writer.

    The injected ``audit_write`` is bound (in the lifespan) to the kernel's
    ``AuditLogger.log(event_type, source, details, task_id, severity)``, whose
    signature these keywords map straight onto.
    """

    def __init__(
        self,
        audit_write: Callable[..., Coroutine[Any, Any, None]],
        logger: logging.Logger | None = None,
    ) -> None:
        self._audit_write = audit_write
        self._logger = logger or _DEFAULT_LOGGER

    async def write(
        self,
        event_type: str,
        *,
        source: str,
        details: dict[str, Any] | None = None,
        task_id: str | None = None,
        severity: str = "info",
    ) -> None:
        await self._audit_write(
            event_type,
            source=source,
            details=details,
            task_id=task_id,
            severity=severity,
        )

    def write_bg(
        self,
        event_type: str,
        *,
        source: str,
        details: dict[str, Any] | None = None,
        task_id: str | None = None,
        severity: str = "info",
    ) -> None:
        """Fire-and-forget audit write — schedule the durable write, never block.

        Mirrors the semantics of ``services.audit_log.audit_log_bg`` (which the
        migrating call sites used directly): the row is written on a background
        task and any failure is swallowed so a telemetry write never breaks the
        pipeline — loudly for warn/critical (a dropped finding row silently
        kills the downstream alert, #303), quietly otherwise. This adapter is
        the forward home of that behavior; ``audit_log_bg`` retires once every
        content site reaches audit through the handle.
        """
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            self._drop_bg(event_type, source, severity, "no running event loop")
            return
        task = loop.create_task(
            self._audit_write(
                event_type,
                source=source,
                details=details,
                task_id=task_id,
                severity=severity,
            )
        )
        task.add_done_callback(
            functools.partial(
                self._on_bg_done,
                event_type=event_type,
                source=source,
                severity=severity,
            )
        )

    def _on_bg_done(
        self,
        task: "asyncio.Task[Any]",
        *,
        event_type: str,
        source: str,
        severity: str,
    ) -> None:
        if task.cancelled():
            return
        exc = task.exception()
        if exc is not None:
            self._drop_bg(event_type, source, severity, f"task failed: {exc!r}")

    def _drop_bg(
        self, event_type: str, source: str, severity: str, reason: str
    ) -> None:
        if (severity or "").lower() in _LOUD_BG_SEVERITIES:
            self._logger.error(
                "DROPPED %s audit (%s): event=%s source=%s — will NOT reach "
                "the alert pipeline",
                severity, reason, event_type, source,
            )
        else:
            self._logger.debug(
                "audit.write_bg dropped %s (%s)", event_type, reason
            )


def _make_log_metric_emit(logger: logging.Logger) -> Callable[..., None]:
    """A ``metric`` backing that routes samples to the structured log.

    The v1 sink for the ``metric`` capability. The kernel has no Prometheus
    *push* seam yet — ``services/metrics_exporter.py`` is pull-based predefined
    gauges — and a generic dynamic-label emitter would trip the
    metric-cardinality governance hard-edge (Glad-Labs/poindexter#666). Until
    that seam exists, metric samples go to the structured log (Loki-queryable),
    so the capability is *real and non-raising* rather than a silent no-op or a
    fail-loud stub. When the push seam lands, ``build_kernel_platform`` passes a
    real ``metric_emit`` and this default is no longer used — rent the
    implementation, keep the interface.
    """

    def _emit(name: str, value: float = 1.0, /, **labels: str) -> None:
        logger.debug(
            "metric %s=%s",
            name,
            value,
            extra={"platform_metric": {"name": name, "value": value, "labels": labels}},
        )

    return _emit


class KernelPlatform:
    """The full ``Platform`` (all capabilities) for the running kernel.

    Constructed once from the live services; per-module scoping is applied by
    wrapping this in ``plugins.platform.ScopedPlatform`` (Wave 2). Structurally
    satisfies the ``Platform`` Protocol.
    """

    def __init__(
        self,
        *,
        site_config: Any,
        pool: Any,
        dispatch: Callable[..., Awaitable[Any]],
        audit_write: Callable[..., Coroutine[Any, Any, None]],
        metric_emit: Callable[..., None] | None = None,
        logger: logging.Logger | None = None,
    ) -> None:
        _logger = logger or _DEFAULT_LOGGER
        self._config = _ConfigAdapter(site_config)
        self._secret = _SecretAdapter(site_config)
        self._dispatch = _DispatchAdapter(dispatch)
        self._db = _DbAdapter(pool)
        self._log = _LogAdapter(_logger)
        # No Prometheus push seam yet — default the metric sink to the
        # structured log (see ``_make_log_metric_emit``).
        self._metric = _MetricAdapter(metric_emit or _make_log_metric_emit(_logger))
        self._audit = _AuditAdapter(audit_write, logger=_logger)

    @property
    def config(self) -> ConfigCapability:
        return self._config

    @property
    def secret(self) -> SecretCapability:
        return self._secret

    @property
    def dispatch(self) -> DispatchCapability:
        return self._dispatch

    @property
    def db(self) -> DbCapability:
        return self._db

    @property
    def log(self) -> LogCapability:
        return self._log

    @property
    def metric(self) -> MetricCapability:
        return self._metric

    @property
    def audit(self) -> AuditCapability:
        return self._audit


def build_kernel_platform(
    *,
    site_config: Any,
    pool: Any,
    dispatch: Callable[..., Awaitable[Any]],
    audit_logger: Any,
    logger: logging.Logger | None = None,
) -> KernelPlatform:
    """Construct the live ``KernelPlatform`` from already-initialised services.

    The boot-time factory called by ``main.py``'s lifespan (Wave 3b of Seam 1,
    Glad-Labs/poindexter#667). It adapts the kernel services the lifespan has
    already brought up — the loaded ``SiteConfig``, the asyncpg ``pool``, the
    ``dispatch_complete`` router callable, and the kernel ``AuditLogger`` — into
    the capability interfaces of ``plugins.platform``.

    ``audit`` binds to ``audit_logger.log`` (the kernel's
    ``AuditLogger.log(event_type, source, details, task_id, severity)``, which
    the ``AuditCapability.write`` keywords map straight onto). ``metric`` is
    left to the ``KernelPlatform`` default (structured-log sink) — there is no
    Prometheus push seam yet. Services are passed in (not imported here) so this
    factory stays import-cheap and unit-testable without the worker's heavy deps.
    """
    return KernelPlatform(
        site_config=site_config,
        pool=pool,
        dispatch=dispatch,
        audit_write=audit_logger.log,
        logger=logger,
    )


__all__ = ["KernelPlatform", "build_kernel_platform"]
