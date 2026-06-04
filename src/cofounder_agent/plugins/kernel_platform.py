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

import logging
from typing import Any, Awaitable, Callable

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
    """Forwards the ``audit`` capability to the injected ``audit_log`` writer."""

    def __init__(self, audit_write: Callable[..., Awaitable[None]]) -> None:
        self._audit_write = audit_write

    async def write(self, event_type: str, /, **details: Any) -> None:
        await self._audit_write(event_type, **details)


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
        audit_write: Callable[..., Awaitable[None]],
        metric_emit: Callable[..., None],
        logger: logging.Logger | None = None,
    ) -> None:
        self._config = _ConfigAdapter(site_config)
        self._secret = _SecretAdapter(site_config)
        self._dispatch = _DispatchAdapter(dispatch)
        self._db = _DbAdapter(pool)
        self._log = _LogAdapter(logger or _DEFAULT_LOGGER)
        self._metric = _MetricAdapter(metric_emit)
        self._audit = _AuditAdapter(audit_write)

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


__all__ = ["KernelPlatform"]
