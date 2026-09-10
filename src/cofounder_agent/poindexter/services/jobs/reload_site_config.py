"""ReloadSiteConfigJob — periodically refresh the in-memory site_config cache.

`services.site_config.site_config` caches all non-secret `app_settings` rows
in memory. Without this job, the cache is populated once at lifespan startup
and never refreshed — so any SQL UPDATE (via the settings API, admin UI, or
psql) is invisible to the running worker until a container restart.

See internal tracker for the root-cause writeup. This job pairs with the
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
from utils.exception_format import describe_exception

logger = logging.getLogger(__name__)


class ReloadSiteConfigJob:
    """Refresh site_config's in-memory snapshot from app_settings."""

    name = "reload_site_config"
    description = "Periodic refresh of site_config from app_settings (internal tracker)"
    # Tunable via app_settings["plugin.job.reload_site_config.schedule"] once
    # the job scheduler honors per-plugin overrides; default is every minute.
    schedule = "every 1 minute"
    idempotent = True

    async def run(self, pool: Any, config: dict[str, Any]) -> JobResult:
        # The plugin scheduler seeds the lifespan-bound SiteConfig instance
        # at ``config["_site_config"]`` (per CLAUDE.md "DI seam" pattern).
        # In the worker that instance (``main.py``'s ``_site_cfg``) is the
        # SAME object the FastAPI request path reads: ``main.py`` passes it
        # to ``build_container(..., site_config=_site_cfg)``, so
        # ``app.state.container.site_config`` — what
        # ``get_site_config_dependency`` returns to route handlers — IS this
        # object. Calling ``.reload()`` here therefore refreshes the cache
        # for routes, services, and the wired modules alike, within one
        # cycle. (Before that wiring, the container held a separate
        # SiteConfig and runtime changes never reached routes until a
        # restart — the 2026-06-17 hot-reload gap.)
        site_config = config.get("_site_config")
        if site_config is None:
            return JobResult(
                ok=False,
                detail="no site_config in config (job dispatcher seeding broken?)",
                changes_made=0,
            )

        if pool is None:
            return JobResult(ok=False, detail="no pool available", changes_made=0)

        # Quality-model watch (Glad-Labs/poindexter#985): snapshot the
        # judge/writer model pins BEFORE the reload so a change made via any
        # write surface (console, CLI, MCP, raw SQL) is announced within one
        # cycle. The 2026-06-29 approval collapse traced to a critic-model
        # swap that no surface announced — it ran 5+ weeks unnoticed.
        watch_keys = [
            k.strip()
            for k in (
                site_config.get(
                    "quality_model_watch_keys",
                    "pipeline_critic_model,qa_fallback_critic_model,"
                    "pipeline_writer_model,pipeline_local_writer_model,"
                    "qa_rewrite_model",
                ) or ""
            ).split(",")
            if k.strip()
        ]
        before = {k: site_config.get(k, None) for k in watch_keys}

        try:
            count = await site_config.reload(pool)
        except Exception as e:  # noqa: BLE001 — site_config.reload swallows
            #                                     and returns 0 itself, but
            #                                     belt-and-suspenders here
            logger.warning("[reload_site_config] reload failed: %s", describe_exception(e))
            return JobResult(ok=False, detail=f"reload failed: {describe_exception(e)}", changes_made=0)

        logger.debug("[reload_site_config] reloaded %d keys", count)
        changed = {
            k: (before.get(k), site_config.get(k, None))
            for k in watch_keys
            if site_config.get(k, None) != before.get(k)
        }
        for key, (old, new) in changed.items():
            try:
                from utils.findings import emit_finding

                emit_finding(
                    source="services.jobs.reload_site_config",
                    kind="quality_model_changed",
                    title=f"Quality-critical model pin changed: {key}",
                    body=(
                        f"`{key}` changed from {old!r} to {new!r} (detected on "
                        f"the site_config reload cycle — the write surface did "
                        f"not announce it). Judge/writer swaps shift the whole "
                        f"QA distribution; calibrate the new judge with "
                        f"`poindexter model-eval run --slot critic` before "
                        f"trusting threshold-sensitive decisions "
                        f"(Glad-Labs/poindexter#985)."
                    ),
                    severity="warn",
                    dedup_key=f"quality_model_changed:{key}:{new}",
                    extra={"key": key, "old": old, "new": new},
                )
            except Exception:  # noqa: BLE001 — announcement is best-effort
                logger.warning(
                    "[reload_site_config] failed to emit quality_model_changed "
                    "finding for %s", key,
                )

        return JobResult(
            ok=True,
            detail=(
                f"site_config refreshed ({count} keys)"
                + (f"; quality-model changes: {sorted(changed)}" if changed else "")
            ),
            changes_made=len(changed),
            metrics={"quality_model_changes": len(changed)},
        )
