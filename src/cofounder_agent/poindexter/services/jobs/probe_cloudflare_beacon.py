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

import asyncio
import logging
from typing import Any

from poindexter.plugins.job import JobResult
from poindexter.services.metrics_exporter import CLOUDFLARE_BEACON_REACHABLE
from poindexter.utils.findings import emit_finding

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Flap control (stack#3573). A single failed POST is not an outage: the
# Worker answers in ~50 ms whenever anyone checks, yet a lone ConnectTimeout
# was producing ~3 findings a day. Two knobs, both app_settings:
#   attempts          — POSTs per run before the run counts as a failure
#   min_consecutive   — failed RUNS in a row before a finding is emitted
# The gauge always carries the raw per-run result; the finding is the gated
# signal. Streak state is process-local (resets on worker restart, which is
# the right bias: a fresh process starts innocent).
# ---------------------------------------------------------------------------
ATTEMPTS_KEY = "cloudflare_beacon_probe_attempts"
MIN_CONSECUTIVE_KEY = "cloudflare_beacon_probe_min_consecutive_failures"
CONNECT_TIMEOUT_KEY = "cloudflare_beacon_probe_connect_timeout_seconds"
DEFAULT_ATTEMPTS = 2
DEFAULT_MIN_CONSECUTIVE = 2
DEFAULT_CONNECT_TIMEOUT_S = 5
_RETRY_DELAY_S = 1.0
_consecutive_failures = 0


def _reset_state() -> None:
    """Test hook — clear the failure streak."""
    global _consecutive_failures
    _consecutive_failures = 0


def _int_setting(sc: Any, key: str, default: int) -> int:
    """Read an integer app_setting through the DI'd SiteConfig; a missing or
    unparseable value falls back to the code default (probe tunables, not
    gates)."""
    try:
        raw = sc.get(key, "")
    except Exception:  # noqa: BLE001 — stubbed site_config
        # silent-ok: a probe tunable falling back to its code default.
        return default
    try:
        return int(str(raw).strip()) if str(raw).strip() else default
    except ValueError:
        return default


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

        global _consecutive_failures
        attempts = max(1, _int_setting(sc, ATTEMPTS_KEY, DEFAULT_ATTEMPTS))
        min_consecutive = max(1, _int_setting(sc, MIN_CONSECUTIVE_KEY, DEFAULT_MIN_CONSECUTIVE))
        connect_s = float(_int_setting(sc, CONNECT_TIMEOUT_KEY, DEFAULT_CONNECT_TIMEOUT_S))
        reachable = False
        detail = ""
        for attempt in range(1, attempts + 1):
            try:
                # Explicit connect sub-cap so a stuck SYN/DNS can't stall the
                # probe past its own budget. Empty JSON body → Worker returns
                # 204 and writes nothing (side-effect-free health ping).
                async with httpx.AsyncClient(
                    timeout=httpx.Timeout(8.0, connect=connect_s)
                ) as client:
                    resp = await client.post(beacon_url, json={})
                reachable = 200 <= resp.status_code < 300
                detail = f"HTTP {resp.status_code}"
            except Exception as e:  # noqa: BLE001 — any failure ⇒ unreachable
                reachable = False
                detail = f"{type(e).__name__}: {e}"
            if reachable or attempt == attempts:
                break
            # stack#3573: 41 findings in 14 days, every one a ConnectTimeout
            # on a single POST, while the Worker answered 204 in ~50 ms from
            # both the host and the worker container whenever anyone looked.
            # One short retry inside the same run absorbs the edge/DNS blip.
            await asyncio.sleep(_RETRY_DELAY_S)

        # Process-global gauge; exposed on the next /metrics scrape. This is
        # the raw truth of THIS run — the PoindexterCloudflareBeaconDown rule
        # carries its own `for: 11m` debounce; the finding below is gated
        # separately on a streak of failed runs.
        CLOUDFLARE_BEACON_REACHABLE.set(1 if reachable else 0)

        if reachable:
            _consecutive_failures = 0
            return JobResult(
                ok=True,
                detail=f"beacon reachable ({detail})",
                changes_made=0,
            )

        _consecutive_failures += 1
        if _consecutive_failures < min_consecutive:
            logger.warning(
                "[BEACON_PROBE] beacon unreachable (%s) — %d/%d consecutive "
                "run(s); finding withheld until the streak reaches %d",
                detail, _consecutive_failures, min_consecutive, min_consecutive,
            )
            return JobResult(
                ok=True,
                detail=(
                    f"beacon UNREACHABLE ({detail}); streak "
                    f"{_consecutive_failures}/{min_consecutive}, finding withheld"
                ),
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
