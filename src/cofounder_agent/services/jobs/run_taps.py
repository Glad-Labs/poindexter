"""RunTapsJob — schedule the integrations tap_runner.

Without this wrapper the only path that calls ``tap_runner.run_all`` is
``poindexter taps run`` from the CLI. That meant external_taps last
fired 2026-05-01 17:26 UTC (manual invocation) and stayed silent for
8 days — every tap row had ``schedule = "every 6 hours"`` /
``"every 1 hour"`` etc. but nothing was actually firing the runner
itself.

This Job sits in the PluginScheduler at hourly cadence and walks every
enabled tap. The shortest per-tap schedule today is ``hackernews:
every 1 hour``, so hourly is the right floor; slower taps (e.g.
``knowledge: every 12 hours``) get woken more often than their row
asks, which is harmless because the handlers are idempotent.

A future refinement would respect each tap's per-row ``schedule`` +
``last_run_at`` and skip not-yet-due rows inside ``run_all`` — see
``deletion-candidates.md`` follow-up.
"""

from __future__ import annotations

import logging
from typing import Any

from plugins.job import JobResult

logger = logging.getLogger(__name__)


class RunTapsJob:
    name = "run_taps"
    description = "Walk enabled external_taps rows and invoke each handler"
    schedule = "every 1 hour"
    idempotent = True

    async def run(self, pool: Any, config: dict[str, Any]) -> JobResult:
        from services.integrations import tap_runner

        site_config = config.get("_site_config")
        try:
            summary = await tap_runner.run_all(pool, site_config=site_config)
        except Exception as exc:
            logger.exception("RunTapsJob failed: %s", exc)
            return JobResult(ok=False, detail=str(exc), changes_made=0)

        if summary.total_failed:
            return JobResult(
                ok=False,
                detail=(
                    f"{summary.total_failed} tap(s) failed; "
                    f"records collected={summary.total_records}"
                ),
                changes_made=summary.total_records,
            )

        return JobResult(
            ok=True,
            detail=f"records collected={summary.total_records}",
            changes_made=summary.total_records,
        )
