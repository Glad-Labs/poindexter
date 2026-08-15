"""ProbeNarrationFailureJob — page when the narration lane is dead, not just log it.

Narration is fail-soft by design: a TTS failure emits one per-render
``narration_synthesis_failed`` warn finding and the video ships silent and
caption-less. Findings route to Discord — and during the 2026-08-13
chatterbox outage they did, 30+ times over three days, scrolling past like
routine noise while three silent renders reached the operator's approval
queue and were all rejected. Per-render warn is the right granularity for
forensics and the wrong one for noticing; this probe adds the aggregate,
escalating view (the ``probe_hero_fallback`` pattern).

It pages ``critical`` — the findings router's floor routes critical to
Telegram and never cools it — on either of two triggers:

1. **Render-time failures clustered**: ``narration_synthesis_failed`` count
   over a trailing window clears a threshold. Catches installs running with
   ``media_tts_gate_enabled=false`` and mid-render TTS deaths the dispatch
   preflight can't see.
2. **The TTS engine is down and stays down**: a live probe of the configured
   engine's ``/health`` (the same URL resolution the dispatch gate uses)
   fails for N consecutive runs. This trigger matters because the dispatch
   gate turns a TTS outage into *silent deferral* — no renders, no failure
   findings — so trigger 1 alone would go quiet exactly when the outage is
   total. Consecutive-run state comes from this job's own ``job_run``
   metrics history in ``audit_log`` (the scheduler's generic metrics sink),
   so the job stays stateless; when that history is unavailable (metrics
   capture disabled) the probe-trigger degrades to requiring a failure
   finding alongside the live miss, rather than paging on one blip.

Every run returns counts as ``JobResult.metrics`` so narration health is
graphable while healthy, and so run N+1 can read run N's probe result.
"""

from __future__ import annotations

import logging
from typing import Any

import httpx

from plugins.job import JobResult
from utils.findings import emit_finding

logger = logging.getLogger(__name__)

_ENABLED_KEY = "narration_failure_probe_enabled"
_WINDOW_HOURS_KEY = "narration_failure_window_hours"
_MIN_COUNT_KEY = "narration_failure_min_count"
_MIN_CONSECUTIVE_KEY = "narration_failure_min_consecutive_probes"
_DEFAULT_WINDOW_HOURS = 6
_DEFAULT_MIN_COUNT = 3
_DEFAULT_MIN_CONSECUTIVE = 3

_FINDING_KIND = "narration_failure_streak"
_SOURCE_KIND = "narration_synthesis_failed"

# One row per failed narration synthesis (long/short/podcast lane each emit
# their own). ``extra.task_id`` + ``extra.reason`` are set by
# ``_narration_render._emit_narration_failed_finding``.
_FAILURES_QUERY = """
    SELECT
        COUNT(*) AS failures,
        COUNT(DISTINCT details->'extra'->>'task_id') AS tasks,
        MODE() WITHIN GROUP (
            ORDER BY details->'extra'->>'reason'
        ) AS top_reason
    FROM audit_log
    WHERE event_type = 'finding'
      AND details->>'kind' = $1
      AND "timestamp" > NOW() - ($2 * INTERVAL '1 hour')
"""

# This job's own prior fires, newest first — written by the scheduler's
# job_run metrics sink (source = job name). Only fires WITH metrics are
# recorded, and this job always returns metrics, so consecutive rows here are
# consecutive runs.
_OWN_HISTORY_QUERY = """
    SELECT details->'metrics'->>'tts_healthy' AS tts_healthy
    FROM audit_log
    WHERE event_type = 'job_run'
      AND source = $1
    ORDER BY "timestamp" DESC
    LIMIT $2
"""


def _cfg_bool(site_config: Any, key: str, default: bool) -> bool:
    return site_config.get_bool(key, default) if site_config is not None else default


def _cfg_int(site_config: Any, key: str, default: int) -> int:
    return site_config.get_int(key, default) if site_config is not None else default


async def _probe_tts(site_config: Any, *, http_client_factory: Any = None) -> tuple[bool | None, str]:
    """Live-probe the configured TTS engine's ``/health``.

    Returns ``(healthy, detail)``; ``healthy=None`` means the probe was
    skipped (TTS disabled by config, or no URL resolvable) and must count as
    neither trigger evidence nor recovery.
    """
    from services.media_infra_health import resolve_tts_health_url
    from services.tts_service import is_tts_enabled

    if site_config is None or not is_tts_enabled(site_config):
        return None, "tts disabled (podcast_tts_enabled) — probe skipped"
    engine, url = resolve_tts_health_url(site_config)
    if not url:
        return None, f"no health URL resolvable for engine {engine!r} — probe skipped"

    if http_client_factory is None:
        http_client_factory = httpx.AsyncClient
    timeout_s = (
        site_config.get_float("media_infra_health_timeout_seconds", 5.0) or 5.0
    )
    try:
        async with http_client_factory(
            timeout=httpx.Timeout(timeout_s, connect=min(timeout_s, 3.0)),
        ) as client:
            resp = await client.get(url)
    except Exception as exc:  # noqa: BLE001 — unreachable IS the signal here
        return False, f"{engine} {url} unreachable ({exc.__class__.__name__}: {exc})"
    if 200 <= resp.status_code < 300:
        return True, f"{engine} healthy"
    return False, f"{engine} {url} returned HTTP {resp.status_code}"


class ProbeNarrationFailureJob:
    """Escalate a sustained narration/TTS outage to a critical page."""

    name = "probe_narration_failure"
    description = (
        "Watchdog for the narration lane — pages (critical → Telegram) when "
        "TTS failures cluster or the configured TTS engine stays down, "
        "instead of letting silent videos scroll past as per-render Discord "
        "warns (the 2026-08-13 chatterbox outage ran two days)"
    )
    schedule = "every 1 hour"
    idempotent = True

    async def run(self, pool: Any, config: dict[str, Any]) -> JobResult:
        if pool is None:
            return JobResult(ok=False, detail="no pool available", changes_made=0)

        site_config = config.get("_site_config")
        if not _cfg_bool(site_config, _ENABLED_KEY, True):
            return JobResult(ok=True, detail="disabled via app_settings", changes_made=0)

        window_hours = max(
            1, _cfg_int(site_config, _WINDOW_HOURS_KEY, _DEFAULT_WINDOW_HOURS),
        )
        min_count = max(1, _cfg_int(site_config, _MIN_COUNT_KEY, _DEFAULT_MIN_COUNT))
        min_consecutive = max(
            1, _cfg_int(site_config, _MIN_CONSECUTIVE_KEY, _DEFAULT_MIN_CONSECUTIVE),
        )

        async with pool.acquire() as conn:
            row = await conn.fetchrow(_FAILURES_QUERY, _SOURCE_KIND, window_hours)
            history = await conn.fetch(
                _OWN_HISTORY_QUERY, self.name, max(0, min_consecutive - 1),
            )

        failures = int((row["failures"] if row else 0) or 0)
        tasks = int((row["tasks"] if row else 0) or 0)
        top_reason = (row["top_reason"] if row else None) or "(no reason recorded)"

        tts_healthy, tts_detail = await _probe_tts(site_config)

        # Consecutive unhealthy observations INCLUDING this run: walk own
        # history newest-first while it says 'false'. A gap (healthy or
        # skipped run, or missing history) breaks the streak — degrading
        # toward fewer pages, never more.
        consecutive = 0
        if tts_healthy is False:
            consecutive = 1
            for h in history:
                if (h["tts_healthy"] or "") == "false":
                    consecutive += 1
                else:
                    break

        metrics = {
            "narration_failures": failures,
            "tasks_affected": tasks,
            "window_hours": window_hours,
            # str for stable round-tripping through the job_run JSONB sink;
            # 'skipped' keeps a disabled-TTS install from ever counting as
            # unhealthy history.
            "tts_healthy": {True: "true", False: "false", None: "skipped"}[tts_healthy],
            "tts_detail": tts_detail,
            "consecutive_unhealthy": consecutive,
        }

        failures_trigger = failures >= min_count
        probe_trigger = tts_healthy is False and (
            consecutive >= min_consecutive
            # No usable history (metrics capture off / first runs): demand a
            # render-time failure alongside the live miss before paging.
            or (len(history) == 0 and min_consecutive > 1 and failures > 0)
        )

        if failures_trigger or probe_trigger:
            trigger = "failures" if failures_trigger else "probe"
            emit_finding(
                source="services.jobs.probe_narration_failure",
                kind=_FINDING_KIND,
                title=(
                    f"narration lane is down — {failures} TTS failure(s) / "
                    f"{tasks} task(s) in {window_hours}h; live probe: {tts_detail}"
                ),
                body=(
                    f"The narration lane is failing in aggregate "
                    f"(trigger: {trigger}).\n\n"
                    f"- `narration_synthesis_failed` findings in the last "
                    f"{window_hours}h: {failures} across {tasks} task(s)\n"
                    f"- dominant reason: {top_reason}\n"
                    f"- live TTS probe: {tts_detail}"
                    f" ({consecutive} consecutive unhealthy run(s))\n\n"
                    "Renders hit while TTS is down ship SILENT and "
                    "caption-less (fail-soft by design), and with "
                    "`media_tts_gate_enabled` on, dispatch is deferring — "
                    "either way no watchable video ships until TTS is back.\n\n"
                    "If the engine is chatterbox: it is an opt-in compose "
                    "profile a plain `docker compose up -d` does NOT start "
                    "and a stack stop leaves stopped — "
                    "`docker compose --profile tts-hq up -d chatterbox`. "
                    "For Speaches: `docker start poindexter-speaches`. "
                    "Deferred pieces re-render on their own once the probe "
                    "passes (media_reconciliation resets burned re-dispatch "
                    "caps).\n\n"
                    f"Tune via `{_MIN_COUNT_KEY}` / `{_WINDOW_HOURS_KEY}` / "
                    f"`{_MIN_CONSECUTIVE_KEY}`."
                ),
                severity="critical",
                dedup_key=f"{_FINDING_KIND}:{window_hours}h",
                extra=metrics,
            )
            return JobResult(
                ok=True,
                detail=(
                    f"narration lane unhealthy ({trigger}): {failures} "
                    f"failure(s)/{window_hours}h, probe={tts_detail} — paged"
                ),
                changes_made=1,
                metrics=metrics,
            )

        return JobResult(
            ok=True,
            detail=(
                f"{failures} narration failure(s) in {window_hours}h "
                f"(min {min_count}); probe: {tts_detail}"
            ),
            changes_made=0,
            metrics=metrics,
        )
