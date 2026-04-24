"""Declarative integrations framework.

Pattern: each integration surface (webhooks, retention policies, external
taps, publishing adapters, object stores, ...) has its own table plus a
handler function registered by name. A row in the table + an enabled
flag = a live integration. No code change, no deploy.

See ``docs/architecture/declarative-data-plane-rfc-2026-04-24.md`` for
the full design.

Core pieces:

- :mod:`registry` — maps ``"surface.handler_name"`` to callables.
  Decorator-style registration so handlers self-register on import.
- :mod:`secret_resolver` — single audited path for decrypting a row's
  signing/auth secret via ``site_config.get_secret``. Every integration
  surface routes through this function so the GH-107 raw-get bug class
  cannot reappear inside the framework.
- ``handlers/`` — one module per surface with the handler
  implementations (``handlers/webhook_revenue.py``, etc.). Each module
  is imported at framework startup to populate the registry.
"""

from services.integrations.registry import (
    HandlerRegistrationError,
    dispatch,
    lookup,
    register_handler,
    registered_names,
)
from services.integrations.secret_resolver import resolve_secret

__all__ = [
    "HandlerRegistrationError",
    "dispatch",
    "lookup",
    "register_handler",
    "registered_names",
    "resolve_secret",
]
