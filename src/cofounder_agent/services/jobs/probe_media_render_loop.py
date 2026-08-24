"""ProbeMediaRenderLoopJob — page when one piece keeps failing its render.

A single ``render_failed`` finding is routine: renders lose races with
deploys, GPU eviction, sidecar cold boots — the re-dispatch machinery exists
precisely to absorb that. The failure mode this probe exists for is the SAME
task failing over and over: a permanently-broken frozen input (the 2026-08-24
case was an ambient-bed path frozen as a per-container /tmp tempfile,
poindexter#1021) fails identically on every attempt, and the daily cap-reset
self-heal re-arms it each morning. Each doomed attempt loads the full video
model stack before dying, so five days of that churn helped push the host
into global OOM before any human was paged.

Per-attempt findings are the right granularity for forensics and the wrong
one for noticing (the hero-fallback probe learned the same lesson). This
probe adds the per-task aggregate: over a trailing window it counts render
failures grouped by task, and pages CRITICAL once any task clears the
threshold — a task that fails that many separate attempts is structurally
unable to render, and retrying is now doing damage rather than healing.

Every run returns the counts as ``JobResult.metrics`` so the scheduler's
``job_run`` audit sink keeps render-failure clustering graphable even while
healthy.
"""

from __future__ import annotations

import logging
from typing import Any

from plugins.job import JobResult
from utils.findings import emit_finding

logger = logging.getLogger(__name__)

_ENABLED_KEY = "media_render_loop_probe_enabled"
_WINDOW_HOURS_KEY = "media_render_loop_window_hours"
_MIN_FAILURES_KEY = "media_render_loop_min_failures"
# 48h default deliberately spans two cap-reset cooldown days: a loop that
# survives ONE daily re-arm is the pattern worth paging on.
_DEFAULT_WINDOW_HOURS = 48
# 6 = ~1.5 wedge cycles across both lanes (each dispatch fails long + short);
# transient infra blips produce 1-2, the 08-24 loop produced ~14/task.
_DEFAULT_MIN_FAILURES = 6

_FINDING_KIND = "media_render_loop"
# Both kinds are per-attempt failures that carry extra.task_id and repeat
# forever on a broken frozen input: render_failed (compositor/renderer) and
# shot_list_invalid (frozen shot list fails schema).
_SOURCE_KINDS = ("render_failed", "shot_list_invalid")

_LOOP_QUERY = """
    SELECT
        details->'extra'->>'task_id' AS task_id,
        COUNT(*) AS failures
    FROM audit_log
    WHERE event_type = 'finding'
      AND details->>'kind' = ANY($1::text[])
      AND COALESCE(details->'extra'->>'task_id', '') != ''
      AND "timestamp" > NOW() - ($2 * INTERVAL '1 hour')
    GROUP BY 1
    ORDER BY 2 DESC
"""


def _cfg_bool(site_config: Any, key: str, default: bool) -> bool:
    return site_config.get_bool(key, default) if site_config is not None else default


def _cfg_int(site_config: Any, key: str, default: int) -> int:
    return site_config.get_int(key, default) if site_config is not None else default


class ProbeMediaRenderLoopJob:
    """Emit a critical finding when one task's renders keep failing."""

    name = "probe_media_render_loop"
    description = (
        "Watchdog for permanent render-failure loops — pages when the SAME "
        "piece fails its video render repeatedly, instead of letting the "
        "re-dispatch self-heal churn model loads for days (poindexter#1021)"
    )
    schedule = "every 6 hours"
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
        min_failures = max(
            2, _cfg_int(site_config, _MIN_FAILURES_KEY, _DEFAULT_MIN_FAILURES),
        )

        async with pool.acquire() as conn:
            rows = await conn.fetch(
                _LOOP_QUERY, list(_SOURCE_KINDS), window_hours,
            )

        per_task = {
            str(r["task_id"]): int(r["failures"] or 0) for r in rows or []
        }
        looping = {t: n for t, n in per_task.items() if n >= min_failures}
        total_failures = sum(per_task.values())

        metrics = {
            "render_failures": total_failures,
            "tasks_failing": len(per_task),
            "tasks_looping": len(looping),
            "window_hours": window_hours,
        }

        if looping:
            worst = max(looping.values())
            listing = ", ".join(
                f"{task} ({n}×)" for task, n in
                sorted(looping.items(), key=lambda kv: -kv[1])
            )
            emit_finding(
                source="services.jobs.probe_media_render_loop",
                kind=_FINDING_KIND,
                title=(
                    f"{len(looping)} piece(s) stuck in a render-failure loop "
                    f"(worst {worst}× in {window_hours}h)"
                ),
                body=(
                    f"The same task(s) keep failing their video render — "
                    f"{listing} over the last {window_hours}h. A repeat count "
                    f"this high means the failure is structural (a broken "
                    f"frozen input, not an outage): every retry loads the full "
                    f"video model stack, fails identically, and the daily "
                    f"cap-reset re-arms it — the exact churn that helped OOM "
                    f"the host on 2026-08-24 (poindexter#1021).\n\n"
                    f"Read the failure reason from the latest render_failed "
                    f"finding for the task (Findings board, or audit_log "
                    f"details). A `soundtrack_path missing: '/tmp/...'` means "
                    f"a pre-#1021 frozen tempfile path — regenerate the bed "
                    f"into ~/.poindexter/video/ and jsonb_set the latest "
                    f"pipeline_versions row. After fixing the input, re-arm "
                    f"with: UPDATE pipeline_tasks SET "
                    f"media_pipeline_cap_reset_count = 0, "
                    f"media_pipeline_redispatch_count = 0 WHERE task_id = "
                    f"'<task>'.\n\n"
                    f"Tune this alert via `{_MIN_FAILURES_KEY}` / "
                    f"`{_WINDOW_HOURS_KEY}`."  # nosec B608 - operator-instruction TEXT in a finding body, never executed
                ),
                severity="critical",
                dedup_key=f"{_FINDING_KIND}:{window_hours}h",
                extra={**metrics, "looping_tasks": listing},
            )
            return JobResult(
                ok=True,
                detail=(
                    f"{len(looping)} task(s) looping (worst {worst}× in "
                    f"{window_hours}h) — critical finding emitted"
                ),
                changes_made=1,
                metrics=metrics,
            )

        return JobResult(
            ok=True,
            detail=(
                f"{total_failures} render failure(s) across {len(per_task)} "
                f"task(s) in {window_hours}h (no task ≥ {min_failures})"
            ),
            changes_made=0,
            metrics=metrics,
        )
