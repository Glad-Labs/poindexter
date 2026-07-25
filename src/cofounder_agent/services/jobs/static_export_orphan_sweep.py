"""StaticExportOrphanSweepJob — retire de-published post exports.

When a post leaves the published set (status → ``rejected`` / ``archived`` /
deleted), two artifacts can linger:

* ``static/posts/<slug>.json`` on R2 (the public site reads post content from
  here), and
* the prerendered ``/posts/<slug>`` route on Vercel.

If neither is cleaned up, the route serves a **stale soft-404** — HTTP 200 with
the "Post Not Found" UI plus Next.js's auto-injected
``<meta name="robots" content="noindex">``. Google files those under
"Excluded by 'noindex'" in Search Console instead of dropping them as 404s, so
deleted content lingers in the index reports and stays publicly reachable
(Glad-Labs/poindexter#1146).

This janitor closes the gap on a schedule, independent of *which* takedown path
ran (the gate hard-kill, the publish-gate reject, or a bulk DB update — none of
which currently revalidate). Every cycle it:

1. lists the per-post JSONs on storage,
2. diffs them against the currently-published slugs in Postgres, and
3. for each orphan, deletes the R2 JSON and triggers ISR revalidation of
   ``/posts/<slug>`` — **in that order**, because the page reads from R2 and
   revalidating before deletion would just re-cache the stale 200.

The very first run also clears any historical backlog (e.g. the rejected posts
that were never revalidated).

## Config (``plugin.job.static_export_orphan_sweep``)

- ``config.max_per_run`` (default 50) — cap retirements per cycle so a
  misconfiguration (e.g. an empty published set from a bad query) can't
  mass-delete the bucket in one pass. When the cap truncates, it logs the
  remainder rather than silently dropping it (no silent caps).
"""

from __future__ import annotations

import logging
from typing import Any

from plugins.job import JobResult

logger = logging.getLogger(__name__)

_DEFAULT_MAX_PER_RUN = 50


class StaticExportOrphanSweepJob:
    name = "static_export_orphan_sweep"
    description = (
        "Delete + ISR-revalidate de-published post JSONs so takedowns return "
        "a true 404 instead of a stale soft-404 (#1146)"
    )
    schedule = "every 30 minutes"
    idempotent = True  # Re-running with no orphans is a no-op.

    async def run(self, pool: Any, config: dict[str, Any]) -> JobResult:
        # The scheduler seeds the run-bound SiteConfig into config["_site_config"]
        # (same contract as static_export_reconciliation).
        site_config = config.get("_site_config")
        if site_config is None:
            return JobResult(
                ok=False,
                detail="no _site_config bound to job run",
                changes_made=0,
            )

        max_per_run = int(config.get("max_per_run", _DEFAULT_MAX_PER_RUN))

        # Import inside run() so the module imports cleanly without the export
        # service's heavier deps at registry-scan time.
        from services.static_export_service import (
            _list_exported_post_slugs,
            _retire_slug,
        )

        # Source of truth: the published set in Postgres.
        try:
            async with pool.acquire() as conn:
                rows = await conn.fetch(
                    "SELECT slug FROM posts WHERE status = 'published'",
                )
        except Exception as e:  # noqa: BLE001
            logger.exception(
                "static_export_orphan_sweep: published-slug query failed: %s", e,
            )
            return JobResult(
                ok=False, detail=f"DB query failed: {e}", changes_made=0,
            )

        published = {r["slug"] for r in rows if r["slug"]}

        exported = await _list_exported_post_slugs(site_config=site_config)
        if not exported:
            # Either nothing exported yet, or storage isn't reachable/configured
            # (list_keys returns [] in both cases). Nothing to do either way.
            return JobResult(
                ok=True,
                detail="no exported post JSONs found (or storage unconfigured)",
                changes_made=0,
                metrics={"published": len(published), "exported": 0, "orphans_retired": 0},
            )

        orphans = [slug for slug in exported if slug not in published]

        # Safety valve: never retire more than max_per_run in a single cycle.
        # A bad published set (e.g. a transient empty query result) shouldn't be
        # able to wipe the whole bucket at once.
        truncated = len(orphans) > max_per_run
        if truncated:
            logger.warning(
                "static_export_orphan_sweep: %d orphans found, retiring %d this "
                "cycle (max_per_run=%d); the remaining %d will be swept next run",
                len(orphans), max_per_run, max_per_run, len(orphans) - max_per_run,
            )
            orphans = orphans[:max_per_run]

        retired = 0
        for slug in orphans:
            try:
                await _retire_slug(slug, site_config=site_config)
                retired += 1
            except Exception as e:  # noqa: BLE001
                logger.warning(
                    "static_export_orphan_sweep: retire failed for %s: %s",
                    slug, e,
                )

        detail = f"retired {retired} orphaned post JSON(s)"
        if truncated:
            detail += " (capped this cycle; more remain)"

        return JobResult(
            ok=True,
            detail=detail,
            changes_made=retired,
            metrics={
                "published": len(published),
                "exported": len(exported),
                "orphans_retired": retired,
            },
        )
