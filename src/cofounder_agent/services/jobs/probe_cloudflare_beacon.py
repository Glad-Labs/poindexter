"""ProbeCloudflareBeaconJob — active outage detector for the page-views beacon.

The ViewTracker beacon on the public site POSTs page-view pings to a
Cloudflare Worker (``infrastructure/cloudflare/page-views-beacon/``), which
writes them to CF Analytics Engine; :class:`SyncCloudflareAnalyticsJob`
ingests them into ``page_views`` every 5 minutes. If that Worker is down,
page-view analytics silently stop flowing — nothing in the pipeline errors,
the numbers just quietly flatline. The only pre-existing signal was a 3-day
freshness cross-check (poindexter#671), which is far too slow to notice a
real outage.

This job is the *active* detector. Every 5 minutes it POSTs an empty body
``{}`` to the configured beacon URL and checks for a 2xx — the Worker
returns ``204`` for an empty / no-slug POST, so ``{}`` is a side-effect-free
health ping that writes nothing. The result is published on two channels:

  - the ``poindexter_cloudflare_beacon_reachable`` Prometheus gauge (0/1),
    which the static rule ``PoindexterCloudflareBeaconDown`` alerts on
    (→ AlertManager → Discord), and
  - on an unreachable probe, an :func:`emit_finding` (kind
    ``cloudflare_beacon_unreachable``, severity ``warn``) that routes through
    FindingsAlertRouter to the Discord ops channel and shows on the Findings
    dashboard.

Why a separate job and not an inline check in ``metrics_exporter.refresh_metrics``:
that refresh runs on every Prometheus scrape (15-30s) and the beacon is an
external-internet endpoint, so probing it inline would put a cross-internet
round-trip on every scrape — coupling ``/metrics`` latency to Cloudflare and
burning ~3-6k Worker invocations a day. A 5-minute job decouples both. The
gauge it sets is a process-global singleton and this job runs in the same
worker process as the ``/metrics`` handler (PluginScheduler is started in
``main.py``'s lifespan), so the value is exposed on the next scrape.

``cloudflare_beacon_url`` is read from ``app_settings`` via the SiteConfig DI
seam. The key was dropped as an orphan 2026-06-03 when no reader existed;
this job is the reader that makes it load-bearing again (re-seeded empty by
``settings_defaults``). An empty URL means the operator hasn't configured a
beacon, so we skip the probe and leave the gauge healthy — the absence of
config must never read as an outage.
"""

from __future__ import annotations

import logging
from typing import Any

from plugins.job import JobResult
from services.metrics_exporter import CLOUDFLARE_BEACON_REACHABLE
from utils.findings import emit_finding

logger = logging.getLogger(__name__)


class ProbeCloudflareBeaconJob:
    name = "probe_cloudflare_beacon"
    description = (
        "POST a side-effect-free health ping to the Cloudflare page-views "
        "beacon Worker; publish reachability as a Prometheus gauge and emit "
        "a finding when it is unreachable."
    )
    schedule = "every 5 minutes"
    idempotent = True

    async def run(self, pool: Any, config: dict[str, Any]) -> JobResult:
        # DI seam (glad-labs-stack#330). No site_config ⇒ we can't read the
        # URL; treat as "nothing to watch" and keep the gauge healthy rather
        # than emitting a false outage.
        sc = config.get("_site_config")
        if sc is None:
            CLOUDFLARE_BEACON_REACHABLE.set(1)
            return JobResult(
                ok=True,
                detail="no _site_config in job config — skipping probe",
                changes_made=0,
            )

        beacon_url = (sc.get("cloudflare_beacon_url", "") or "").strip()
        if not beacon_url:
            # Operator hasn't configured a beacon — nothing to probe. Keep the
            # gauge at healthy so an unconfigured install never alerts.
            CLOUDFLARE_BEACON_REACHABLE.set(1)
            return JobResult(
                ok=True,
                detail="cloudflare_beacon_url unset — skipping probe",
                changes_made=0,
            )

        try:
            import httpx
        except ImportError:
            return JobResult(ok=False, detail="httpx not available", changes_made=0)

        reachable = False
        detail = ""
        try:
            # Explicit connect sub-cap so a stuck SYN/DNS can't stall the
            # probe past its own budget. Empty JSON body → Worker returns 204
            # and writes nothing (side-effect-free health ping).
            async with httpx.AsyncClient(
                timeout=httpx.Timeout(8.0, connect=3.0)
            ) as client:
                resp = await client.post(beacon_url, json={})
            reachable = 200 <= resp.status_code < 300
            detail = f"HTTP {resp.status_code}"
        except Exception as e:  # noqa: BLE001 — any failure ⇒ unreachable
            detail = f"{type(e).__name__}: {e}"

        # Process-global gauge; exposed on the next /metrics scrape.
        CLOUDFLARE_BEACON_REACHABLE.set(1 if reachable else 0)

        if reachable:
            return JobResult(
                ok=True,
                detail=f"beacon reachable ({detail})",
                changes_made=0,
            )

        logger.warning(
            "[BEACON_PROBE] Cloudflare page-views beacon unreachable "
            "(%s): %s",
            beacon_url,
            detail,
        )
        # emit_finding is fire-and-forget and never raises (utils/findings.py).
        emit_finding(
            source="probe_cloudflare_beacon",
            kind="cloudflare_beacon_unreachable",
            severity="warn",
            title="Cloudflare page-views beacon unreachable",
            body=(
                f"A health POST to the page-views beacon Worker did not return "
                f"2xx ({detail}). First-party page-view analytics ingestion is "
                f"stalled until the Cloudflare Worker recovers — check the "
                f"Worker (dash.cloudflare.com → Workers & Pages → "
                f"page-views-beacon) and `wrangler tail`, and verify "
                f"`cloudflare_beacon_url` ({beacon_url}) is correct."
            ),
            dedup_key="cloudflare_beacon_unreachable",
        )
        # ok=True: the probe itself ran successfully — an unreachable beacon is
        # the *observed result*, not a job crash. The gauge + finding carry the
        # outage signal; marking the job red here would just double-alert and
        # trigger apscheduler back-off on a working probe.
        return JobResult(
            ok=True,
            detail=f"beacon UNREACHABLE ({detail}) — finding emitted",
            changes_made=0,
        )
