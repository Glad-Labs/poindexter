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

Idempotent *sequentially* — NOT overlap-safe, hence ``idempotent =
False`` (the scheduler maps that to apscheduler ``max_instances=1``, so
a fire whose predecessor is still in flight is skipped, loudly, via the
``EVENT_JOB_MAX_INSTANCES`` listener in plugins/scheduler.py). The
2026-08-15 incident is why: the 02:06 walk hung for 80 minutes on two
wedged internal_rag handlers, the 03:06 fire started anyway, and two
full tap walks ran concurrently — doubling LLM/embedding load on a GPU
that was already OOM. One walk at a time; the next tick catches up.

A future refinement would respect each tap's per-row ``schedule`` +
``last_run_at`` and skip not-yet-due rows inside ``run_all`` — see
``deletion-candidates.md`` follow-up.
"""

from __future__ import annotations

import logging
from typing import Any

from plugins.job import JobResult
from utils.exception_format import describe_exception

logger = logging.getLogger(__name__)


class RunTapsJob:
    name = "run_taps"
    description = "Walk enabled external_taps rows and invoke each handler"
    schedule = "every 1 hour"
    # Overlap guard (2026-08-15 incident — see module docstring): re-running
    # is safe, running CONCURRENTLY is not. False → max_instances=1.
    idempotent = False

    async def run(self, pool: Any, config: dict[str, Any]) -> JobResult:
        from services.integrations import tap_runner

        site_config = config.get("_site_config")
        try:
            summary = await tap_runner.run_all(pool, site_config=site_config)
        except Exception as exc:
            logger.exception("RunTapsJob failed: %s", describe_exception(exc))
            return JobResult(ok=False, detail=describe_exception(exc), changes_made=0)

        # Deferrals are reported but never flip `ok`. A tap declined by GPU
        # admission (game_mode) is the scheduler working, not an outage, and
        # folding it into `ok=False` emitted a `job_failure` finding claiming
        # a fault while the rest of the walk collected records normally
        # (poindexter#1015). Per-tap detail now rides the `tap_failure`
        # finding, keyed per tap; this aggregate stays a summary.
        deferred_note = ""
        if summary.total_deferred:
            deferred_names = [t.name for t in summary.taps if t.deferred]
            deferred_note = (
                f"; {summary.total_deferred} deferred "
                f"({', '.join(deferred_names)})"
            )

        if summary.total_failed:
            failed_names = [t.name for t in summary.taps if not t.ok]
            failed_errors = "; ".join(
                f"{t.name}: {t.error}" for t in summary.taps if not t.ok
            )
            return JobResult(
                ok=False,
                detail=(
                    f"{summary.total_failed} tap(s) failed ({', '.join(failed_names)}): "
                    f"{failed_errors}; records collected={summary.total_records}"
                    f"{deferred_note}"
                ),
                changes_made=summary.total_records,
            )

        return JobResult(
            ok=True,
            detail=(
                f"records collected={summary.total_records}{deferred_note}"
            ),
            changes_made=summary.total_records,
        )
