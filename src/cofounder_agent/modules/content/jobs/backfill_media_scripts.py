"""BackfillMediaScriptsJob — recover pieces stranded with empty video scripts.

The scripts-side sibling of ``backfill_video_shot_lists``. Stage-1's
``generate_media_scripts`` fail-softs when the GPU is busy
(``media_scripts skipped — GPU busy``): the piece publishes with
``video_long_script = ''`` / ``short_summary_script = ''`` frozen into its
latest ``pipeline_versions`` row, and nothing ever retries the calls. That is
not merely a missing artifact — the narration atom's deliberate
``video_long_script → podcast_script`` fallback means the piece's long video
narrates the ENTIRE podcast episode (2026-08-15: a 371-second video voicing a
5,375-char podcast script, plus a sibling piece that only escaped the same
fate because TTS happened to be down — both operator-rejected). The shot-list
backfill even compounds it: it happily plans shots over the podcast-script
fallback, laundering the stranded piece back into the render queue.

Each cycle takes a small batch of stranded pieces and runs the shared
``media_regen`` core: regenerate the video scripts (video-only mode — the
published podcast artifacts are untouched), replan the shot lists over the
NEW long script, write back, delete the bad-script renders, reset their
approvals, and clear the dispatch marker so the media dispatcher re-renders.

Population guards:

- ``podcast_script`` non-empty — the proxy for "this piece runs the media
  block" (dev_diary pieces have no media nodes and must never enter).
- no APPROVED video/video_short approval — an operator sign-off outranks
  this job's opinion of the scripts; healing would delete an approved asset.

Deliberately batch-1 and idempotent-by-effect: each piece costs two script
LLM calls plus the director + review on the shared GPU. A piece whose regen
skips (GPU busy again) is left untouched for the next cycle.

Module home (not ``services/jobs/``): drives Stage-1 stages via the regen
core — same Seam 2 reasoning as the shot-list sibling.
"""

from __future__ import annotations

import logging
from typing import Any

from plugins.job import JobResult
from utils.exception_format import describe_exception
from utils.findings import emit_finding

logger = logging.getLogger(__name__)

_ENABLED_KEY = "backfill_media_scripts_enabled"
_BATCH_KEY = "backfill_media_scripts_batch"
_DEFAULT_BATCH = 1

_FINDING_KIND = "media_scripts_backfilled"

# Stranded = publishable, media-bearing (podcast_script present), long video
# script empty, and no operator-approved video standing over it. NOT filtered
# on media_pipeline_dispatched_at — the sibling job learned that lesson: every
# stranded piece was already claimed by the run that froze the empty scripts.
_STRANDED_SQL = """
    SELECT pt.task_id
      FROM pipeline_tasks pt
      JOIN LATERAL (
           SELECT stage_data
             FROM pipeline_versions
            WHERE task_id = pt.task_id
            ORDER BY created_at DESC
            LIMIT 1
      ) pv ON TRUE
     WHERE pt.status IN ('approved', 'published')
       AND COALESCE(pv.stage_data -> 'task_metadata' ->> 'podcast_script', '') != ''
       AND COALESCE(pv.stage_data -> 'task_metadata' ->> 'video_long_script', '') = ''
       AND NOT EXISTS (
           SELECT 1
             FROM media_approvals ma
             JOIN posts p ON p.id = ma.post_id
            WHERE p.metadata->>'pipeline_task_id' = pt.task_id
              AND ma.medium IN ('video', 'video_short')
              AND ma.status = 'approved'
       )
     ORDER BY pt.updated_at DESC
     LIMIT $1
"""


def _cfg_bool(sc: Any, key: str, default: bool) -> bool:
    return sc.get_bool(key, default) if sc is not None else default


def _cfg_int(sc: Any, key: str, default: int) -> int:
    return sc.get_int(key, default) if sc is not None else default


class BackfillMediaScriptsJob:
    """Regenerate empty frozen video scripts so renders stop voicing the
    podcast episode (or shipping silent)."""

    name = "backfill_media_scripts"
    description = (
        "Recovers pieces whose Stage-1 media_scripts was skipped for a busy "
        "GPU: their empty video_long_script makes the narration fall back to "
        "voicing the whole PODCAST script over the video (2026-08-15 "
        "operator rejections)"
    )
    schedule = "every 6 hours"
    # Real LLM work on the shared GPU — overlapping instances must not stack.
    idempotent = False

    async def run(self, pool: Any, config: dict[str, Any]) -> JobResult:
        if pool is None:
            return JobResult(ok=False, detail="no pool available", changes_made=0)

        site_config = config.get("_site_config")
        if not _cfg_bool(site_config, _ENABLED_KEY, True):
            return JobResult(ok=True, detail="disabled via app_settings", changes_made=0)

        batch = max(1, _cfg_int(site_config, _BATCH_KEY, _DEFAULT_BATCH))

        try:
            rows = await pool.fetch(_STRANDED_SQL, batch)
        except Exception as exc:  # noqa: BLE001 — never crash the scheduler
            logger.warning("[SCRIPTS_BACKFILL] stranded query failed: %s", exc)
            return JobResult(ok=False, detail=f"query failed: {describe_exception(exc)}", changes_made=0)

        if not rows:
            return JobResult(
                ok=True,
                detail="no stranded pieces",
                changes_made=0,
                metrics={"backfilled": 0, "attempted": 0},
            )

        from modules.content.media_regen import regen_video_scripts
        from services.di_wiring import build_platform_for_subprocess

        platform = build_platform_for_subprocess(pool, site_config)
        if platform is None:
            # Without the capability handle every LLM call inside the regen
            # no-ops; the core would then refuse to write, so the whole run
            # is a guaranteed no-op — skip loudly instead.
            return JobResult(
                ok=False,
                detail="no platform handle — skipping (regen would no-op)",
                changes_made=0,
            )

        class _DBShim:
            """The stages only reach through to ``.pool``."""

            def __init__(self, p: Any) -> None:
                self.pool = p

        backfilled = 0
        attempted = 0
        healed_tasks: list[str] = []

        for row in rows:
            task_id = str(row["task_id"])
            attempted += 1
            try:
                outcome = await regen_video_scripts(
                    task_id,
                    pool=pool,
                    site_config=site_config,
                    platform=platform,
                    database_service=_DBShim(pool),
                    apply=True,
                )
            except Exception as exc:  # noqa: BLE001 — one bad piece must not
                # abort the batch; the next cycle retries it.
                logger.warning(
                    "[SCRIPTS_BACKFILL] regen raised for %s: %s", task_id[:8], exc,
                )
                continue

            if not outcome.ok:
                # GPU busy again, or the stages produced nothing usable. The
                # core wrote nothing, so the piece is exactly as it was —
                # leave it for the next cycle rather than fake progress.
                logger.info(
                    "[SCRIPTS_BACKFILL] %s — %s; leaving for the next cycle",
                    task_id[:8], outcome.detail,
                )
                continue

            backfilled += 1
            healed_tasks.append(task_id[:8])
            logger.info(
                "[SCRIPTS_BACKFILL] %s — %s; marker cleared, re-render queued",
                task_id[:8], outcome.detail,
            )

        metrics = {"backfilled": backfilled, "attempted": attempted}

        if backfilled:
            emit_finding(
                source="modules.content.jobs.backfill_media_scripts",
                kind=_FINDING_KIND,
                title=(
                    f"Regenerated empty video scripts for {backfilled} piece(s) "
                    f"({', '.join(healed_tasks)})"
                ),
                body=(
                    f"Regenerated video narration scripts + shot lists for "
                    f"{backfilled} of {attempted} attempted piece(s), deleted "
                    f"their bad-script renders, reset their video approvals, "
                    f"and cleared their dispatch markers — the media pipeline "
                    f"will re-render them from the new scripts.\n\n"
                    f"These pieces froze an empty `video_long_script` when "
                    f"Stage-1's media_scripts was skipped for a busy GPU; "
                    f"until now their long videos narrated the whole PODCAST "
                    f"script via the narration atom's fallback (operator-"
                    f"rejected 2026-08-15). Podcast scripts/audio untouched."
                ),
                severity="info",
                dedup_key=f"{_FINDING_KIND}:{backfilled}",
                extra=metrics,
            )

        return JobResult(
            ok=True,
            detail=f"backfilled {backfilled}/{attempted} stranded piece(s)",
            changes_made=backfilled,
            metrics=metrics,
        )
