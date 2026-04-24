"""Handler registry for the declarative integrations framework.

Every integration surface dispatches to a handler by name. Handlers
register themselves via the :func:`register_handler` decorator at
import time. Dispatch is a plain dictionary lookup — no dynamic
import, no entry_point discovery, no reflection. Keeps the hot path
obvious and test-coverable.

## Naming

Handlers are namespaced by surface so the same short name can be
reused across surfaces. Example: ``revenue_event_writer`` registered
under both ``webhook`` and ``tap`` surfaces writes to
``revenue_events`` whether invoked from an inbound webhook or a
Singer tap record stream.

The canonical form is ``"<surface>.<name>"``. Both forms are accepted
at lookup time; the surface prefix is required at registration.
"""

from __future__ import annotations

import logging
from collections.abc import Awaitable, Callable
from typing import Any

logger = logging.getLogger(__name__)


class HandlerRegistrationError(RuntimeError):
    """Raised when a handler is registered twice or looked up unknown."""


Handler = Callable[..., Awaitable[Any]]

# Keyed by "<surface>.<name>". Surfaces allocate their own namespace
# (e.g. "webhook.revenue_event_writer") so short names can repeat.
_REGISTRY: dict[str, Handler] = {}


def register_handler(surface: str, name: str) -> Callable[[Handler], Handler]:
    """Decorator — register a handler under ``<surface>.<name>``.

    Duplicate registration is a hard error. The framework imports every
    handler module at startup, so a duplicate means two modules
    claimed the same name. Failing loudly beats silent override.
    """
    if not surface or "." in surface:
        raise HandlerRegistrationError(
            f"surface must be a non-empty string without dots; got {surface!r}"
        )
    if not name or "." in name:
        raise HandlerRegistrationError(
            f"name must be a non-empty string without dots; got {name!r}"
        )

    key = f"{surface}.{name}"

    def decorator(fn: Handler) -> Handler:
        existing = _REGISTRY.get(key)
        if existing is not None and existing is not fn:
            raise HandlerRegistrationError(
                f"handler already registered: {key!r} -> {existing!r}"
            )
        _REGISTRY[key] = fn
        return fn

    return decorator


def lookup(surface: str, name: str) -> Handler:
    """Return the handler registered under ``<surface>.<name>``.

    Raises :class:`HandlerRegistrationError` if not found.
    """
    key = f"{surface}.{name}"
    handler = _REGISTRY.get(key)
    if handler is None:
        raise HandlerRegistrationError(
            f"no handler registered under {key!r}. "
            f"Known: {sorted(_REGISTRY.keys())!r}"
        )
    return handler


async def dispatch(
    surface: str,
    name: str,
    payload: Any,
    *,
    site_config: Any,
    row: dict[str, Any],
    pool: Any = None,
) -> Any:
    """Invoke the named handler.

    The contract every handler honors:

    - Receives the parsed payload (dict, bytes — handler-specific)
    - Receives ``site_config`` for secret decryption + config lookups
    - Receives ``row`` — the integration row as a dict, so the handler
      can read its own config from ``row["config"]`` and see its own
      ``name`` / ``metadata`` without another DB query
    - Receives ``pool`` — asyncpg pool for writing to target tables
      (revenue_events, subscriber_events, alert_events, etc.). May be
      ``None`` for pure-compute handlers; DB-touching handlers should
      raise a clear error when it's missing

    Exceptions propagate. The caller (e.g. the webhook dispatcher or
    retention runner) is responsible for logging, counter updates, and
    deciding whether to retry.
    """
    handler = lookup(surface, name)
    return await handler(payload, site_config=site_config, row=row, pool=pool)


def registered_names(surface: str | None = None) -> list[str]:
    """Return all registered handler keys, optionally filtered by surface."""
    if surface is None:
        return sorted(_REGISTRY.keys())
    prefix = f"{surface}."
    return sorted(k for k in _REGISTRY if k.startswith(prefix))
