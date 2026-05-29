"""CLI lifecycle helpers for the SiteConfig constructor-DI migration.

Single public entry point: :func:`container_for_cli` — an
:class:`asynccontextmanager` that builds an :class:`AppContainer` for
the duration of a CLI subcommand's ``_impl()`` body.

Design doc: ``docs/architecture/2026-05-28-site-config-di-migration.md``
(SiteConfig DI migration PR 2 — entry point wireup).

Why a context manager rather than a free function: the container is the
DI seam every CLI subcommand will eventually pull services from. A
manager gives us a single, future-proof place to add per-command
teardown (close shared http clients, drain async queues, flush traces,
etc.) without revisiting every subcommand the migration touches.

Today the body is a no-op — ``AppContainer`` is a dataclass with
``cached_property`` services and nothing inside it owns lifecycle
resources of its own. PR 3+ migrates services one-at-a-time; the first
service that holds a resource gets a matching teardown here.

Usage::

    from poindexter.cli._lifecycle import container_for_cli

    async def _impl():
        pool = await asyncpg.create_pool(_dsn(), min_size=1, max_size=2)
        try:
            async with container_for_cli(pool) as container:
                # Use container.foo_service.do_thing() once services
                # have migrated. Until then this still gives PR 3+
                # one fewer change to make per command.
                ...
        finally:
            await pool.close()
"""

from __future__ import annotations

from contextlib import asynccontextmanager
from typing import Any, AsyncIterator

from services.bootstrap import build_container
from services.container import AppContainer
from services.logger_config import get_logger

logger = get_logger(__name__)


@asynccontextmanager
async def container_for_cli(pool: Any) -> AsyncIterator[AppContainer]:
    """Build an :class:`AppContainer` for a CLI ``_impl()`` body.

    Per ``feedback_no_silent_defaults``: ``build_container`` raises
    ``RuntimeError`` loudly when the ``app_settings`` query fails. We
    propagate that error to the caller — a CLI command running with
    no config is almost certainly running against the wrong DB.

    The container has no service entries during the migration period
    (PR 2). Each migration PR adds a ``cached_property`` for one
    service, and the calling subcommand swaps its inline construction
    for ``container.<service>``.

    Args:
        pool: An asyncpg-style connection pool (or anything that
            exposes the same ``fetch`` / ``acquire`` surface). The
            CLI's outer ``_impl()`` body still owns the pool's
            lifecycle — this helper neither opens nor closes it.

    Yields:
        A constructed :class:`AppContainer` ready for service lookups.
    """
    container = await build_container(pool)
    try:
        yield container
    finally:
        # No teardown needed today — container is just a dataclass +
        # cached_property services with no owned resources. Reserved
        # for future per-CLI cleanup (close http clients, flush
        # traces, drain queues, etc.) once a migrated service holds
        # a resource of its own.
        pass


__all__ = ["container_for_cli"]
