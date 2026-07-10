"""ReapStaleActivityJob — mark orphaned running live_activity rows as stale.

A producer that dies mid-run (worker killed, brain crash) leaves its
``live_activity`` row 'running' forever. The read's freshness window already
hides these from the live pulse, but without a reaper they'd never move to the
recent trail and would accumulate. This job runs every minute and flips running
rows whose heartbeat lapsed past ``live_activity_reaper_seconds`` to
'stale'+finished.

``activity_silent`` so this high-frequency housekeeping job doesn't write its
own live_activity row every minute (it would drown the pulse in reaper noise).
"""
from __future__ import annotations

from typing import Any

from plugins.job import JobResult
from services.live_activity import reap_stale


class ReapStaleActivityJob:
    name = "reap_stale_activity"
    description = "Mark stale live_activity running rows (heartbeat lapsed)"
    schedule = "every 1 minute"
    idempotent = True
    activity_silent = True  # don't write a ledger row for the reaper itself

    async def run(self, pool: Any, config: dict[str, Any]) -> JobResult:
        sc = config.get("_site_config")
        secs = int(sc.get("live_activity_reaper_seconds", "300")) if sc else 300
        n = await reap_stale(pool, reaper_seconds=secs)
        return JobResult(ok=True, detail=f"reaped {n}", changes_made=n)


__all__ = ["ReapStaleActivityJob"]
