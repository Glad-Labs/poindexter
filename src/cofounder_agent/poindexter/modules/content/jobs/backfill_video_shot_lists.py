"""BackfillVideoShotListsJob — recover pieces stranded without a shot list.

Stage-1's ``generate_video_shot_list`` fail-softs when the GPU is busy
(``video_director skipped — GPU busy beyond its wait budget``). The piece keeps
``video_shot_list = {}`` and publishes anyway; Stage 2 then pays for TTS,
transcription and caption alignment, skips both render lanes, and reports
``QA'd 0 asset(s)`` as a success. Nothing retries the director, so a transient
GPU spike costs that piece its video permanently (poindexter#1001).

Measured 2026-08-08: **27** publishable pieces in that state — every piece
created since ~08-01 — while the only surface saying anything was a
``Media drift: N missing video`` count nobody could act on.

This job is the recovery half (``dispatch_media_pipeline``'s eligibility gate
is the prevention half). Each cycle it takes a small batch of stranded pieces,
re-runs the director over the script the render will actually narrate, writes
the shot lists back into the piece's latest ``pipeline_versions`` row, and
clears ``media_pipeline_dispatched_at`` so the media dispatcher picks the piece
up on its next tick.

The director runs with the piece's ``niche_slug``. The director and its reviewer
resolve the per-niche media policy from ``context["niche_slug"]``: the house
style, the style and subject policy, and whether the presenter may appear. A
context without it quietly falls back to the GLOBAL policy — on prod
(2026-09-25) that drops the glad-labs house style and, because the global
``stylized`` policy forbids the photoreal presenter persona, every presenter
shot. Same niche-blind shape as stack#3928 (Stage-2 dispatch) and stack#3932
(media regen).

Each regenerated list then gets the director's self-critique
(``review_video_shot_list``), the second pass the canonical_blog graph and
``media_regen`` both run over every director list. A stranded piece cleared
Gate 1 with no plan, so nothing else looks at a backfilled plan before it
renders. Non-halting, as in the graph: a review that raises or skips for a busy
GPU leaves the director's draft to be written.

Owned by the content module rather than ``services/jobs/`` because it
drives the Stage-1 director: from the kernel that import would be a Seam 2
(kernel→module) violation, from inside the module it is ordinary.

Deliberately small-batch and idempotent-by-effect: the director and its review
are real LLM calls on the shared GPU, so a big sweep would starve the live
pipeline — the condition that created the backlog in the first place. A piece
that fails again is simply left for the next cycle; it is no worse off than
before.
"""

from __future__ import annotations

import json
import logging
from typing import Any

from poindexter.plugins.job import JobResult
from poindexter.utils.exception_format import describe_exception
from poindexter.utils.findings import emit_finding

logger = logging.getLogger(__name__)

_ENABLED_KEY = "backfill_video_shot_lists_enabled"
_BATCH_KEY = "backfill_video_shot_lists_batch"
_DEFAULT_BATCH = 2

_FINDING_KIND = "video_shot_list_backfilled"

# Stranded = publishable, has the narration script the director plans against,
# produced no video, and carries no renderable shot list. NOT filtered on
# media_pipeline_dispatched_at: all 27 were already claimed, so a
# marker-filtered query returns a comforting zero.
_STRANDED_SQL = """
    SELECT pt.task_id,
           -- The director + reviewer resolve the niche's media policy from it.
           pt.niche_slug,
           pv.id            AS version_id,
           pv.title         AS title,
           pv.content       AS content,
           pv.stage_data -> 'task_metadata' ->> 'podcast_script'    AS podcast_script,
           pv.stage_data -> 'task_metadata' ->> 'video_long_script' AS video_long_script,
           -- The 9:16 lane is planned from its OWN script; without it the
           -- director returns a long list only and the short lane silently
           -- stays un-renderable — half a recovery that reads as a whole one.
           pv.stage_data -> 'task_metadata' ->> 'short_summary_script'
               AS short_summary_script
      FROM pipeline_tasks pt
      JOIN LATERAL (
           SELECT id, title, content, stage_data
             FROM pipeline_versions
            WHERE task_id = pt.task_id
            ORDER BY created_at DESC
            LIMIT 1
      ) pv ON TRUE
     WHERE pt.status IN ('approved', 'published')
       AND pv.stage_data -> 'task_metadata' ->> 'podcast_script' IS NOT NULL
       AND pv.stage_data -> 'task_metadata' ->> 'podcast_script' != ''
       AND NOT EXISTS (
           SELECT 1 FROM media_assets ma
            WHERE ma.task_id = pt.task_id AND ma.type = 'video'
       )
       AND CASE
             WHEN jsonb_typeof(
                    pv.stage_data -> 'task_metadata'
                      -> 'video_shot_list' -> 'shots') = 'array'
             THEN jsonb_array_length(
                    pv.stage_data -> 'task_metadata'
                      -> 'video_shot_list' -> 'shots')
             ELSE 0
           END = 0
     ORDER BY pt.updated_at DESC
     LIMIT $1
"""

# Write the regenerated lists into the SAME version row the query read, so a
# concurrently-written newer version is never clobbered.
_WRITE_SQL = """
    UPDATE pipeline_versions
       SET stage_data = jsonb_set(
               jsonb_set(
                   stage_data,
                   '{task_metadata,video_shot_list}',
                   $2::jsonb,
                   true),
               '{task_metadata,short_shot_list}',
               $3::jsonb,
               true)
     WHERE id = $1
"""

# Un-retire the piece: the dispatcher only considers unclaimed rows, and every
# stranded piece was consumed by the run that produced no video.
_UNCLAIM_SQL = """
    UPDATE pipeline_tasks
       SET media_pipeline_dispatched_at = NULL
     WHERE task_id = $1
"""


def _cfg_bool(sc: Any, key: str, default: bool) -> bool:
    return sc.get_bool(key, default) if sc is not None else default


def _cfg_int(sc: Any, key: str, default: int) -> int:
    return sc.get_int(key, default) if sc is not None else default


def _shots_of(shot_list: Any) -> int:
    if isinstance(shot_list, dict):
        shots = shot_list.get("shots")
        return len(shots) if isinstance(shots, list) else 0
    return 0


class BackfillVideoShotListsJob:
    """Regenerate missing shot lists so stranded pieces can render."""

    name = "backfill_video_shot_lists"
    description = (
        "Recovers pieces whose Stage-1 director was skipped for a busy GPU, "
        "leaving them permanently un-renderable (poindexter#1001)"
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
            logger.warning("[SHOTLIST_BACKFILL] stranded query failed: %s", exc)
            return JobResult(ok=False, detail=f"query failed: {describe_exception(exc)}", changes_made=0)

        if not rows:
            return JobResult(
                ok=True,
                detail="no stranded pieces",
                changes_made=0,
                metrics={"backfilled": 0, "attempted": 0, "reviewed": 0},
            )

        from poindexter.modules.content.stages.generate_video_shot_list import (
            GenerateVideoShotListStage,
        )
        from poindexter.modules.content.stages.review_video_shot_list import (
            ReviewVideoShotListStage,
        )
        from poindexter.services.di_wiring import build_platform_for_subprocess

        platform = build_platform_for_subprocess(pool, site_config)
        if platform is None:
            # The director's LLM call goes through the capability handle; with
            # no handle the stage returns a no-op and we would clear markers
            # for pieces that still have no shot list — worse than waiting.
            return JobResult(
                ok=False,
                detail="no platform handle — skipping (would no-op the director)",
                changes_made=0,
            )

        class _DBShim:
            """The stage only reaches through to ``.pool``."""

            def __init__(self, p: Any) -> None:
                self.pool = p

        stage = GenerateVideoShotListStage()
        reviewer = ReviewVideoShotListStage()
        backfilled = 0
        attempted = 0
        reviewed_count = 0

        for row in rows:
            task_id = str(row["task_id"])
            attempted += 1
            # "" rather than None, as the sibling seams do: the declared
            # channel is ``str`` and every consumer reads empty as "no niche".
            niche_slug = (row["niche_slug"] or "").strip()
            if not niche_slug:
                logger.warning(
                    "[SHOTLIST_BACKFILL] %s has no niche_slug — its shot list "
                    "will use the global media policy, not a niche house style",
                    task_id[:8],
                )
            context: dict[str, Any] = {
                "task_id": task_id,
                "niche_slug": niche_slug,
                "title": row["title"] or "",
                "content": row["content"] or "",
                "podcast_script": row["podcast_script"] or "",
                "video_long_script": row["video_long_script"] or "",
                "short_summary_script": row["short_summary_script"] or "",
                "database_service": _DBShim(pool),
                "platform": platform,
                "site_config": site_config,
            }
            try:
                result = await stage.execute(context, {})
            except Exception as exc:  # noqa: BLE001 — one bad piece must not
                # abort the batch; the next cycle retries it.
                logger.warning(
                    "[SHOTLIST_BACKFILL] director raised for %s: %s", task_id[:8], exc,
                )
                continue

            updates = getattr(result, "context_updates", None) or {}
            long_list = updates.get("video_shot_list")
            short_list = updates.get("short_shot_list")
            n_long = _shots_of(long_list)

            if n_long == 0:
                # Skipped again (busy GPU) or produced nothing usable. Leave
                # the piece exactly as it was — including its marker — so this
                # run is a no-op rather than a state change that looks like
                # progress.
                logger.info(
                    "[SHOTLIST_BACKFILL] %s — director produced no shots "
                    "(%s); leaving for the next cycle",
                    task_id[:8], getattr(result, "detail", "no detail"),
                )
                continue

            # The director's self-critique, as the graph runs it after every
            # director list. The reviewer reads the draft from the context and
            # falls back to it on its own failures; a raise here is the same
            # non-halting miss, so the draft is written either way.
            context["video_shot_list"] = long_list
            context["short_shot_list"] = short_list
            reviewed = False
            try:
                review = await reviewer.execute(context, {})
            except Exception as exc:  # noqa: BLE001 — the draft is still a
                # recovery; aborting the write over a failed second pass would
                # leave the piece exactly as stranded as before, for no gain.
                logger.warning(
                    "[SHOTLIST_BACKFILL] review raised for %s — writing the "
                    "director's draft: %s", task_id[:8], describe_exception(exc),
                )
            else:
                r_updates = getattr(review, "context_updates", None) or {}
                # Lane by lane, and never onto an empty list: the review may
                # improve a draft, never undo the recovery.
                if _shots_of(r_updates.get("video_shot_list")):
                    long_list = r_updates["video_shot_list"]
                    n_long = _shots_of(long_list)
                    # The stage's own verdict on the long lane — it hands the
                    # draft back unchanged when its pass fails.
                    reviewed = bool(
                        (getattr(review, "metrics", None) or {}).get("reviewed"),
                    )
                if _shots_of(r_updates.get("short_shot_list")):
                    short_list = r_updates["short_shot_list"]

            try:
                async with pool.acquire() as conn, conn.transaction():
                    await conn.execute(
                        _WRITE_SQL,
                        row["version_id"],
                        json.dumps(long_list),
                        json.dumps(short_list or {}),
                    )
                    await conn.execute(_UNCLAIM_SQL, row["task_id"])
            except Exception as exc:  # noqa: BLE001
                logger.warning(
                    "[SHOTLIST_BACKFILL] persist failed for %s: %s", task_id[:8], exc,
                )
                continue

            backfilled += 1
            reviewed_count += reviewed
            logger.info(
                "[SHOTLIST_BACKFILL] %s (niche=%s) — %d long shot(s) + %d short, "
                "%s, marker cleared; media dispatcher will pick it up",
                task_id[:8], niche_slug or "<none>", n_long, _shots_of(short_list),
                "reviewed" if reviewed else "unreviewed draft",
            )

        metrics = {
            "backfilled": backfilled,
            "attempted": attempted,
            "reviewed": reviewed_count,
        }

        if backfilled:
            emit_finding(
                source="services.jobs.backfill_video_shot_lists",
                kind=_FINDING_KIND,
                title=f"Recovered {backfilled} piece(s) stranded without a shot list",
                body=(
                    f"Regenerated shot lists for {backfilled} of {attempted} "
                    f"attempted piece(s) and cleared their media dispatch "
                    f"markers, so the media pipeline will render them. The "
                    f"director's self-critique revised {reviewed_count} of "
                    f"them; any others carry the director's unreviewed draft "
                    f"(review skipped or failed).\n\n"
                    f"These pieces were published with an empty "
                    f"`video_shot_list` because Stage-1's director was skipped "
                    f"for a busy GPU, which permanently retired them: the media "
                    f"flow paid for TTS and captions, skipped both render lanes, "
                    f"and reported success (poindexter#1001)."
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
