"""Shared process-wide reference to the DatabaseService.

The integrations framework needs a DB pool to look up rows in the
``webhook_endpoints`` table. Handlers accept the pool via the dispatch
contract (``pool`` keyword), but the dispatchers themselves need to
resolve a :class:`DatabaseService` to take the initial row-lookup
path.

Call :func:`set_database_service` once at FastAPI startup (and in the
worker process bootstrap). Legacy helpers that want to opportunistically
route through :func:`services.integrations.outbound_dispatcher.deliver`
use :func:`get_database_service` and fall back when it's unset (early
boot, tests, CLI one-shots).

Why a module-level global rather than DI: the legacy call sites being
migrated (``task_executor._notify_discord``, ``_notify_telegram``,
``revalidation_service.trigger_nextjs_revalidation``) are stand-alone
module functions reached from many call paths, none of which currently
have the DB threaded through. Adding a threading layer to each caller
is disproportionate to the value of the migration. One startup-time
register, one lazy getter.
"""

from __future__ import annotations

from typing import Any

_db_service: Any | None = None


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
