"""IngestCorsairCsvJob — fast-cadence ingest of the local iCUE CSV sensor feed.

## Why a dedicated job (not just RunTapsJob)

``RunTapsJob`` walks *every* enabled tap on a single **hourly** cadence. That
floor is correct for the network taps (hackernews / knowledge / singer) — they
must not hammer remote APIs more often. But ``corsair_csv`` reads a **local**
file that iCUE rewrites every 30 s, so an hourly ingest leaves
``sensor_samples`` up to ~60 min stale *even while iCUE logging is perfectly
healthy*. That stale envelope is what forced the ``data_freshness_probe``
corsair threshold up to 120 min (page on a DEAD sampler, not on the
hourly-runner gap) — which in turn made a real iCUE drop take up to ~2 h to
surface, and a post-reboot drop sit dark far longer.

This job ingests ``corsair_csv`` on its own 5-min cadence via the runner's
existing ``only_names`` scoping, so:

- the other taps are **untouched** (still hourly, no remote-API hammering);
- ``sensor_samples`` stays ~5-10 min fresh, so the probe threshold can safely
  drop to 30 min → an iCUE drop surfaces in ~30-35 min instead of ~120 min;
- Grafana's fan / coolant / PSU-rail panels lag ~5 min, not ~60 min.

The corsair handler is idempotent (``ON CONFLICT DO NOTHING`` on samples + a
monotonic byte cursor), so ``RunTapsJob``'s redundant hourly corsair pass just
finds "no new bytes" and no-ops — the two paths don't fight.

A future ``run_all`` refinement that respects each row's per-row ``schedule``
(the long-noted TODO in ``run_taps.py``) would subsume this job. Until a second
high-frequency *local* tap exists, the scoped job is the lower-blast-radius fix
— it never touches the runner's shared walk.
"""

from __future__ import annotations

import logging
from typing import Any

from plugins.job import JobResult
from utils.exception_format import describe_exception

logger = logging.getLogger(__name__)

_TAP_NAME = "corsair_csv"


class IngestCorsairCsvJob:
    name = "ingest_corsair_csv"
    description = "Fast-cadence ingest of the local iCUE CSV sensor feed"
    schedule = "every 5 minutes"
    idempotent = True

    async def run(self, pool: Any, config: dict[str, Any]) -> JobResult:
        from services.integrations import tap_runner

        site_config = config.get("_site_config")
        try:
            summary = await tap_runner.run_all(
                pool, site_config=site_config, only_names=[_TAP_NAME],
            )
        except Exception as exc:  # noqa: BLE001 — a job must not kill the cycle
            logger.exception("IngestCorsairCsvJob failed: %s", describe_exception(exc))
            return JobResult(ok=False, detail=describe_exception(exc), changes_made=0)

        if summary.total_failed:
            failed_errors = "; ".join(
                f"{t.name}: {t.error}" for t in summary.taps if not t.ok
            )
            return JobResult(
                ok=False,
                detail=(
                    f"{summary.total_failed} tap(s) failed ({failed_errors}); "
                    f"records collected={summary.total_records}"
                ),
                changes_made=summary.total_records,
            )

        return JobResult(
            ok=True,
            detail=f"records collected={summary.total_records}",
            changes_made=summary.total_records,
        )
