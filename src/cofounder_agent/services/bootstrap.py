"""Bootstrap helpers for the SiteConfig constructor-DI migration.

Single public entry point: ``build_container(pool)``. Every entry
point (worker lifespan, Prefect subprocess, CLI command, brain
daemon, test fixture) will call this exactly once at the top of its
async setup to get a fully-loaded ``AppContainer``.

Currently dormant — PR 1 of the migration lands this scaffold; PR 2
wires entry points to call it. See the design doc at
``docs/architecture/2026-05-28-site-config-di-migration.md`` for the
full plan.

This module is deliberately tiny. The DB load logic mirrors the
existing ``_load_site_config`` helper in ``poindexter/cli/schedule.py``
(SELECT key, value FROM app_settings WHERE is_secret = false). The
duplication is intentional during the migration period; the later
cleanup PR folds the CLI helper into this one once every entry point
uses ``build_container``.
"""

from __future__ import annotations

from typing import Any

from services.container import AppContainer
from services.logger_config import get_logger
from services.site_config import SiteConfig

logger = get_logger(__name__)


# SQL kept module-level so the error message in ``build_container``
# can echo it back verbatim when the query fails.
_LOAD_NON_SECRET_SETTINGS_SQL = (
    "SELECT key, value FROM app_settings WHERE is_secret = false"
)


async def build_container(
    pool: Any, *, site_config: SiteConfig | None = None
) -> AppContainer:
    """Build a fully-loaded ``AppContainer`` for the given pool.

    Loads non-secret ``app_settings`` rows into a ``SiteConfig``
    instance (secrets stay async per-call via ``SiteConfig.get_secret``),
    then constructs the container with that config + the pool.

    Pass ``site_config`` to make the container REUSE an instance the
    caller already holds, instead of constructing a fresh one. This is
    the worker hot-reload fix: ``main.py``'s lifespan loads ``_site_cfg``,
    attaches it to ``app.state.site_config``, and seeds it into the
    plugin scheduler — so the periodic ``reload_site_config`` job
    refreshes *that* object. Route handlers, however, read
    ``app.state.container.site_config`` via
    ``get_site_config_dependency``. If the container held a *separate*
    SiteConfig, a runtime ``settings set`` (refreshed into the lifespan
    instance) would never reach routes until a restart. By passing the
    lifespan instance here, the whole worker shares ONE object: the
    instance the scheduler reloads IS the instance routes read.

    Callers that have no pre-loaded instance (CLI, brain daemon, Prefect
    subprocess, tests) omit ``site_config`` and get a fresh one — the
    original behaviour, unchanged.

    Either way the fail-loud probe below runs: the non-secret settings
    query executes and, on a passed instance, atomically refreshes its
    cache from the result (so a stale caller instance is brought current
    at build time, and keys deleted from the DB drop out — matching
    ``SiteConfig.reload`` semantics).

    Per ``feedback_no_silent_defaults``: if the ``app_settings`` query
    fails, this raises ``RuntimeError`` with the SQL it tried in the
    message. We deliberately do NOT silently fall back to an empty
    ``SiteConfig`` — that was the bug class this entire migration is
    designed to eliminate. An entry point hitting this error has a
    real problem (DB pool not actually ready, schema not migrated,
    permissions wrong) and needs to fail loud at the boundary, not
    three layers downstream.

    Args:
        pool: An asyncpg-style connection pool. Anything that exposes
            an awaitable ``fetch(sql)`` method works — the unit tests
            use an ``AsyncMock`` shaped that way.
        site_config: Optional pre-constructed ``SiteConfig`` to reuse.
            When provided, the container holds this exact object (``is``
            identity), and its cache is refreshed from the probe rows.
            When ``None`` (default), a fresh instance is created.

    Returns:
        A constructed ``AppContainer``. During the migration period
        the container exposes essentially no services via
        ``cached_property``; each migration PR adds one.

    Raises:
        RuntimeError: when the non-secret-settings query against
            ``app_settings`` fails, with the SQL echoed in the message.
    """
    if pool is None:
        # Mirror the fail-loud principle for the upstream caller too:
        # passing ``None`` here is always a programming error, not a
        # tolerable degenerate state.
        raise RuntimeError(
            "bootstrap.build_container(pool=None) — entry point must "
            "construct an asyncpg pool before building the container."
        )

    # Fail-loud probe — runs in BOTH the fresh and reuse paths so a
    # broken pool surfaces here, not three layers downstream.
    try:
        rows = await pool.fetch(_LOAD_NON_SECRET_SETTINGS_SQL)
    except Exception as exc:
        # Re-raise as RuntimeError with the SQL embedded so an operator
        # reading the traceback can immediately see what we tried.
        raise RuntimeError(
            "bootstrap.build_container: failed to load app_settings "
            f"({type(exc).__name__}: {exc}). Tried: "
            f"{_LOAD_NON_SECRET_SETTINGS_SQL!r}. Verify the DB pool is "
            "connected and the app_settings table exists."
        ) from exc

    if site_config is None:
        site_config = SiteConfig(pool=pool)
    else:
        # Reuse the caller's instance → one SiteConfig per process.
        # Make sure it can resolve secrets on demand from this pool.
        site_config._pool = pool  # noqa: SLF001

    # Atomic refresh from the probe rows (replace, matching
    # SiteConfig.reload — empty values skipped, like SiteConfig.load).
    site_config._config = {  # noqa: SLF001
        row["key"]: row["value"] for row in rows if row["value"]
    }
    site_config._loaded = True  # noqa: SLF001

    logger.info(
        "[BOOTSTRAP] AppContainer built (site_config loaded %d non-secret settings)",
        len(site_config._config),  # noqa: SLF001
    )
    container = AppContainer(site_config=site_config, pool=pool)

    # Register the container as the process-wide active one (#272
    # capstone). This is the single chokepoint every entry point flows
    # through (main lifespan, CLI ``_lifecycle``, Prefect ``di_wiring``),
    # so registering here makes ``services.container_registry.get_container``
    # return a configured container for every process — which is how the
    # final four ambient-singleton modules (gpu_scheduler / ollama_client /
    # prompt_manager / utils.route_utils) now source their SiteConfig
    # instead of a per-module global wired by ``set_site_config``.
    from services.container_registry import set_container
    set_container(container)

    # ------------------------------------------------------------------
    # Eager-init the Option B facade services. These properties have
    # side-effects (pinning a module-level ``_default_*`` reference) and
    # must run at bootstrap time so the module-level decorator imports
    # (e.g. ``@log_query_performance(...)`` at class definition) see the
    # container-wired SiteConfig instead of the lazy empty fallback the
    # instant the container is built. Read order is intentional — kept
    # explicit rather than hidden behind a registry so adding a new
    # facade service is a one-line change here.
    # ------------------------------------------------------------------
    _ = container.decorators  # pins ``services.decorators._default_decorators``

    return container
