"""Shared process-wide references for the integrations framework.

The integrations framework needs two process-wide handles:

- A :class:`DatabaseService` — for row lookup against the
  ``webhook_endpoints`` table (the dispatchers themselves resolve the
  row before delegating to a handler).
- The lifespan-bound :class:`SiteConfig` — for secret resolution
  (``site_config.get_secret(...)``) when handler callers don't have
  one in scope.

Call :func:`set_database_service` and :func:`set_site_config` once at
FastAPI startup (and in the worker process bootstrap / Prefect
subprocess bootstrap). Legacy helpers that want to opportunistically
route through :func:`services.integrations.outbound_dispatcher.deliver`
use the matching ``get_*`` accessors and fall back when unset (early
boot, tests, CLI one-shots).

Why module-level globals rather than DI: the legacy call sites being
migrated (``task_executor._notify_discord``, ``_notify_telegram``,
``revalidation_service.trigger_nextjs_revalidation``) are stand-alone
module functions reached from many call paths, none of which currently
have the DB pool or SiteConfig threaded through. Adding a threading
layer to each caller is disproportionate to the value of the migration.
One startup-time register, one lazy getter — per handle.

History — the missing-setter regression (2026-05-26):

PR #514 (2026-05-20) introduced a ``_resolve_site_config()`` helper in
:mod:`services.integrations.operator_notify` that imported
``get_site_config`` from this module. The function was never added,
so every call landed in the bare ``except`` and returned ``None``.
``outbound_dispatcher.deliver`` then received ``site_config=None``,
:func:`services.integrations.secret_resolver.resolve_secret` short-
circuited with "no site_config in scope — treating as unconfigured",
and the ``discord_ops`` + ``telegram_ops`` handlers raised "no webhook
URL". The unit test that pinned the fallback mocked over
``_resolve_site_config`` itself, so the broken import never tripped
collection. Resolution: add the missing setter/getter pair here and
wire ``main.py``'s lifespan to populate it alongside the DatabaseService.
"""

from __future__ import annotations

from typing import Any

_db_service: Any | None = None
_site_config: Any | None = None


def set_database_service(db_service: Any) -> None:
    """Register the process-wide DatabaseService reference.

    Called once from ``main.py`` after ``startup_manager.initialize_all_services()``
    returns the DB service. Idempotent — re-setting overwrites the
    previous ref (useful in tests).
    """
    global _db_service
    _db_service = db_service


def get_database_service() -> Any | None:
    """Return the registered DatabaseService, or ``None`` if not yet set.

    Callers that want to opportunistically route through the integrations
    dispatcher should treat ``None`` as "framework not available; fall
    back to legacy behavior".
    """
    return _db_service


def clear_database_service() -> None:
    """Clear the registered reference. For tests."""
    global _db_service
    _db_service = None


def set_site_config(site_config: Any) -> None:
    """Register the process-wide SiteConfig reference.

    Called once from ``main.py``'s lifespan after the DB-backed SiteConfig
    is loaded, and once per Prefect subprocess bootstrap. Idempotent.

    Without this, callers of :func:`services.integrations.operator_notify.notify_operator`
    that don't have a SiteConfig in scope (brain probes, scheduler jobs,
    the alert-dispatcher path, ``_legacy_discord_webhook`` fallback) end
    up with ``site_config=None``, which makes
    :func:`services.integrations.secret_resolver.resolve_secret` short-
    circuit and the corresponding ``discord_ops`` / ``telegram_ops``
    row's secret resolves to empty — the handler then raises
    "no webhook URL" even though the secret is correctly seeded in
    ``app_settings``.
    """
    global _site_config
    _site_config = site_config


def get_site_config() -> Any | None:
    """Return the registered SiteConfig, or ``None`` if not yet set.

    Callers should treat ``None`` as "framework not yet wired" (early
    boot, tests, CLI one-shots) — operator notifications then fall
    back to whatever site_config the caller already had in scope, or
    short-circuit at the secret_resolver with a clear warning.
    """
    return _site_config


def clear_site_config() -> None:
    """Clear the registered SiteConfig reference. For tests."""
    global _site_config
    _site_config = None
