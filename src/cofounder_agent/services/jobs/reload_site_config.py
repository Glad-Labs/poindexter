"""ReloadSiteConfigJob — periodically refresh the in-memory site_config cache.

`services.site_config.site_config` caches all non-secret `app_settings` rows
in memory. Without this job, the cache is populated once at lifespan startup
and never refreshed — so any SQL UPDATE (via the settings API, admin UI, or
psql) is invisible to the running worker until a container restart.

See gitea#280 for the root-cause writeup. This job pairs with the
`/api/settings/reload` endpoint: the endpoint gives interactive UIs a
sub-second turnaround after Save; this scheduled job catches SQL / cron /
out-of-band changes within one cycle.

The reload is cheap: one SELECT across ~300 rows, ~20 KB in memory. Running
it every 60 seconds adds a trivial amount of DB traffic and keeps the
"DB is the source of truth" invariant actually true at runtime.
"""

from __future__ import annotations

import logging
from typing import Any

from plugins.job import JobResult

logger = logging.getLogger(__name__)


class ReloadSiteConfigJob:
    """Refresh site_config's in-memory snapshot from app_settings."""

    name = "reload_site_config"
    description = "Periodic refresh of site_config from app_settings (gitea#280)"
    # Tunable via app_settings["plugin.job.reload_site_config.schedule"] once
    # the job scheduler honors per-plugin overrides; default is every minute.
    schedule = "every 1 minute"
    idempotent = True

    async def run(self, pool: Any, config: dict[str, Any]) -> JobResult:
        # The plugin scheduler seeds the lifespan-bound SiteConfig instance
        # at ``config["_site_config"]`` (per CLAUDE.md "DI seam" pattern).
        # That's the SAME instance every wired module reads from, so
        # calling .reload() on it refreshes the cache for every consumer.
        site_config = config.get("_site_config")
        if site_config is None:
            return JobResult(
                ok=False,
                detail="no site_config in config (job dispatcher seeding broken?)",
                changes_made=0,
            )

        if pool is None:
            return JobResult(ok=False, detail="no pool available", changes_made=0)

        try:
            count = await site_config.reload(pool)
        except Exception as e:  # noqa: BLE001 — site_config.reload swallows
            #                                     and returns 0 itself, but
            #                                     belt-and-suspenders here
            logger.warning("[reload_site_config] reload failed: %s", e)
            return JobResult(ok=False, detail=f"reload failed: {e}", changes_made=0)

        logger.debug("[reload_site_config] reloaded %d keys", count)
        return JobResult(
            ok=True,
            detail=f"site_config refreshed ({count} keys)",
            changes_made=0,
        )
