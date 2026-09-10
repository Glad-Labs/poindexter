"""ProbeHeroFallbackJob — alert when hero animation is silently degrading.

Hero (i2v) shots fall back to a Ken-Burns still whenever wan can't deliver a
clip. That fallback is deliberately soft — a still is far better than a hole
in the video — and it emits one ``hero_render_fallback`` finding per shot at
info level. The failure mode this probe exists for is that *soft and
per-shot* means invisible in aggregate: across the 2026-08-06/-07 renders
EVERY hero fell back for four straight days (wan's idle unloader killing its
own response, the VRAM reclaim ladder killing it mid-generation, then the
writer and vision models crowding it out — poindexter#992), and the operator
noticed the missing motion across three separate deliveries before anything
in the system said a word.

A per-shot info finding is the right granularity for forensics and the wrong
one for noticing. This probe adds the aggregate view: over a trailing window
it counts fallbacks, the distinct pieces affected, and the dominant reason,
and pages once when the count clears a threshold — with a stable dedup_key so
a sustained outage is one page, not one per shot.

Every run returns the counts as ``JobResult.metrics`` so the scheduler's
``job_run`` audit sink makes hero health graphable even while it is healthy.
"""

from __future__ import annotations

import logging
from typing import Any

from plugins.job import JobResult
from utils.findings import emit_finding

logger = logging.getLogger(__name__)

_ENABLED_KEY = "hero_fallback_probe_enabled"
_WINDOW_HOURS_KEY = "hero_fallback_window_hours"
_MIN_FALLBACKS_KEY = "hero_fallback_min_count"
_DEFAULT_WINDOW_HOURS = 24
_DEFAULT_MIN_FALLBACKS = 3

_FINDING_KIND = "hero_fallback_streak"
_SOURCE_KIND = "hero_render_fallback"

# One row per fallen-back shot. ``details`` is the emit_finding envelope, so
# the reason lives at details->'extra'->>'reason' and the piece id is the
# tail of the dedup_key (``hero_render_fallback:<task_id>:<shot_idx>``).
_FALLBACK_QUERY = """
    SELECT
        COUNT(*) AS fallbacks,
        COUNT(DISTINCT split_part(details->>'dedup_key', ':', 2)) AS pieces,
        MODE() WITHIN GROUP (
            ORDER BY details->'extra'->>'reason'
        ) AS top_reason
    FROM audit_log
    WHERE event_type = 'finding'
      AND details->>'kind' = $1
      AND "timestamp" > NOW() - ($2 * INTERVAL '1 hour')
"""


def _cfg_bool(site_config: Any, key: str, default: bool) -> bool:
    return site_config.get_bool(key, default) if site_config is not None else default


def _cfg_int(site_config: Any, key: str, default: int) -> int:
    return site_config.get_int(key, default) if site_config is not None else default


class ProbeHeroFallbackJob:
    """Emit a finding when hero shots keep falling back to stills."""

    name = "probe_hero_fallback"
    description = (
        "Watchdog for hero (i2v) animation — pages when fallbacks to "
        "Ken-Burns stills cluster, instead of letting motion disappear one "
        "info-level finding at a time (poindexter#992)"
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
        min_fallbacks = max(
            1, _cfg_int(site_config, _MIN_FALLBACKS_KEY, _DEFAULT_MIN_FALLBACKS),
        )

        async with pool.acquire() as conn:
            row = await conn.fetchrow(_FALLBACK_QUERY, _SOURCE_KIND, window_hours)

        fallbacks = int((row["fallbacks"] if row else 0) or 0)
        pieces = int((row["pieces"] if row else 0) or 0)
        top_reason = (row["top_reason"] if row else None) or "(no reason recorded)"

        metrics = {
            "hero_fallbacks": fallbacks,
            "pieces_affected": pieces,
            "window_hours": window_hours,
            "top_reason": top_reason,
        }

        if fallbacks >= min_fallbacks:
            emit_finding(
                source="services.jobs.probe_hero_fallback",
                kind=_FINDING_KIND,
                title=(
                    f"{fallbacks} hero shot(s) fell back to stills across "
                    f"{pieces} piece(s) in {window_hours}h"
                ),
                body=(
                    f"Hero (i2v) animation is degrading silently: {fallbacks} "
                    f"shot(s) across {pieces} piece(s) rendered as Ken-Burns "
                    f"stills instead of generated motion in the last "
                    f"{window_hours} hour(s).\n\n"
                    f"Dominant reason: {top_reason}\n\n"
                    f"The fallback itself is working as designed — a still "
                    f"beats a hole — but a cluster means the motion feature is "
                    f"effectively off, which is invisible per-shot (the "
                    f"2026-08 outage ran four days before a human noticed the "
                    f"missing clips, poindexter#992).\n\n"
                    f"Check, in order: `curl localhost:9840/health` (wan up and "
                    f"not degraded); wan-server logs for `OutOfMemory` — if so "
                    f"lower `video_hero_width`/`video_hero_height` (832x480 is "
                    f"the validated default, 1280x704 does not fit a 32GB card "
                    f"beside a co-resident); GPU residents via `nvidia-smi` "
                    f"with `video_hero_evict_ollama` on; and whether shots are "
                    f"being substituted upstream (a substituted shot has no "
                    f"fresh still to animate, so the hero phase no-ops "
                    f"silently — see `video_image_gen_ready_wait_s`).\n\n"
                    f"Tune this alert via `{_MIN_FALLBACKS_KEY}` / "
                    f"`{_WINDOW_HOURS_KEY}`."
                ),
                severity="warn",
                dedup_key=f"{_FINDING_KIND}:{window_hours}h",
                extra=metrics,
            )
            return JobResult(
                ok=True,
                detail=(
                    f"{fallbacks} fallback(s) over {pieces} piece(s) in "
                    f"{window_hours}h — finding emitted"
                ),
                changes_made=1,
                metrics=metrics,
            )

        return JobResult(
            ok=True,
            detail=(
                f"{fallbacks} hero fallback(s) in {window_hours}h "
                f"(below min={min_fallbacks})"
            ),
            changes_made=0,
            metrics=metrics,
        )
