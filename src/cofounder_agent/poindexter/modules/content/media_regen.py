"""Regenerate a task's frozen VIDEO scripts + shot lists in place.

Stage-1 scripts freeze into ``pipeline_versions.stage_data['task_metadata']``
and are re-read at every Stage-2 render, so a defective-or-missing frozen
script can never self-heal through re-renders. The canonical failure this
exists for (2026-08-15): a GPU-admission skip of ``media_scripts`` leaves
``video_long_script`` empty forever, and the narration atom's deliberate
``video_long_script → podcast_script`` fallback then voices the ENTIRE
podcast episode over the video — a 371s video narrating a 5,375-char podcast
script, operator-rejected on sight.

This is the shared core promoted out of ``scripts/regen_media_scripts.py``
(the 2026-08-01 one-off; poindexter#982 called for the promotion). Two
callers:

- ``scripts/regen_media_scripts.py`` — operator CLI, explicit task ids.
- ``modules.content.jobs.backfill_media_scripts`` — scheduled recovery for
  the empty-script population.

It re-runs ``generate_media_scripts`` in **video-only mode**
(``media_scripts_video_only`` context flag: no podcast LLM call, no TTS, no
sting, no ambient bed — first-class replacement for the one-off's
``patch.object`` of the enable helpers, which was module-global and would
have silently disabled podcast audio for any live pipeline run sharing the
worker process), then the director + review stages, and writes back ONLY the
video keys:

    video_long_script, short_summary_script, video_scenes,
    video_shot_list, short_shot_list

``podcast_script`` / podcast audio are deliberately untouched — the episodes
are published artifacts; regenerating their scripts would desync the frozen
text from the shipped audio.

With ``apply=True`` it also heals the render loop: deletes the task's
video/video_short ``media_assets`` rows (they were rendered from the bad
scripts), resets its video ``media_approvals`` to pending, and clears
``media_pipeline_dispatched_at`` so the next ``dispatch_media_pipeline``
cycle re-renders from the fresh scripts.

Module home (not ``services/``): it drives Stage-1 content stages, so a
kernel home would be a Seam 2 (kernel→module) violation — same reasoning as
``modules/content/jobs/backfill_video_shot_lists.py``.
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from typing import Any

logger = logging.getLogger(__name__)

_POST_SQL = """
    SELECT p.id::text AS post_id, p.title, p.content,
           p.metadata->>'seo_title' AS seo_title
      FROM posts p
     WHERE p.metadata->>'pipeline_task_id' = $1
     ORDER BY p.created_at DESC
     LIMIT 1
"""

_LATEST_VERSION_SQL = """
    SELECT id, version FROM pipeline_versions
     WHERE task_id = $1 ORDER BY version DESC LIMIT 1
"""

_WRITEBACK_SQL = """
    UPDATE pipeline_versions
       SET stage_data = jsonb_set(
               stage_data, '{task_metadata}',
               (stage_data->'task_metadata') || $2::jsonb
           )
     WHERE id = $1
"""

_HEAL_ASSETS_SQL = """
    DELETE FROM media_assets
     WHERE task_id = $1 AND type IN ('video', 'video_short')
"""

_HEAL_APPROVALS_SQL = """
    UPDATE media_approvals ma
       SET status = 'pending', decided_at = NULL, decided_by = NULL,
           notes = COALESCE(ma.notes, '') || ' [reset: media scripts regenerated]'
      FROM posts p
     WHERE p.id = ma.post_id
       AND p.metadata->>'pipeline_task_id' = $1
       AND ma.medium IN ('video', 'video_short')
"""

_HEAL_MARKER_SQL = """
    UPDATE pipeline_tasks
       SET media_pipeline_dispatched_at = NULL
     WHERE task_id = $1
"""


@dataclass
class RegenOutcome:
    """One task's regen result — ``detail`` is operator/log-facing."""

    ok: bool
    detail: str
    long_words: int = 0
    short_words: int = 0
    long_shots: int = 0
    short_shots: int = 0
    long_script: str = ""
    short_script: str = ""


async def regen_video_scripts(
    task_id: str,
    *,
    pool: Any,
    site_config: Any,
    platform: Any,
    database_service: Any,
    apply: bool,
) -> RegenOutcome:
    """Regenerate one task's video scripts + shot lists; heal when ``apply``.

    Fail-conservative: any step that produces nothing usable (post/version
    row missing, empty long script, director produced no shot list — e.g. a
    GPU-busy skip inside the stages) returns ``ok=False`` and writes NOTHING,
    so a caller can simply retry a later cycle; the task is no worse off.
    """
    from modules.content.stages.generate_media_scripts import (
        GenerateMediaScriptsStage,
    )
    from modules.content.stages.generate_video_shot_list import (
        GenerateVideoShotListStage,
    )
    from modules.content.stages.review_video_shot_list import (
        ReviewVideoShotListStage,
    )

    row = await pool.fetchrow(_POST_SQL, task_id)
    if not row:
        return RegenOutcome(
            ok=False, detail="no post resolves via pipeline_task_id",
        )
    ver = await pool.fetchrow(_LATEST_VERSION_SQL, task_id)
    if not ver:
        return RegenOutcome(ok=False, detail="no pipeline_versions row")

    # Minimal stage context — mirrors what content_router_service seeds for
    # these stages: content/title (+ seo_title for _resolve_media_title's
    # clean-title preference), the DI seams, and the task id.
    context: dict[str, Any] = {
        "task_id": task_id,
        "title": row["title"] or "",
        "seo_title": row["seo_title"] or "",
        "content": row["content"] or "",
        "site_config": site_config,
        "database_service": database_service,
        "platform": platform,
        # Video-only: skip the podcast LLM call + every audio side effect
        # (published podcast artifacts must not be touched or re-synthesized).
        "media_scripts_video_only": True,
    }

    scripts_result = await GenerateMediaScriptsStage().execute(context, {})
    updates = dict(scripts_result.context_updates or {})
    long_script = (updates.get("video_long_script") or "").strip()
    short_script = (updates.get("short_summary_script") or "").strip()
    if not long_script:
        return RegenOutcome(
            ok=False,
            detail=(
                "regen produced no video_long_script "
                f"({getattr(scripts_result, 'detail', 'no detail')}) — frozen "
                "metadata untouched"
            ),
        )
    # Thread the fresh scripts to the director + review (they read context).
    context.update({k: v for k, v in updates.items() if k != "stages"})

    director_result = await GenerateVideoShotListStage().execute(context, {})
    context.update(
        {k: v for k, v in (director_result.context_updates or {}).items() if k != "stages"},
    )
    review_result = await ReviewVideoShotListStage().execute(context, {})
    context.update(
        {k: v for k, v in (review_result.context_updates or {}).items() if k != "stages"},
    )

    new_meta = {
        "video_long_script": long_script,
        "short_summary_script": short_script,
        "video_scenes": context.get("video_scenes") or [],
        "video_shot_list": context.get("video_shot_list") or {},
        "short_shot_list": context.get("short_shot_list") or {},
    }
    long_shots = len((new_meta["video_shot_list"] or {}).get("shots", []) or [])
    short_shots = len((new_meta["short_shot_list"] or {}).get("shots", []) or [])
    if not new_meta["video_shot_list"]:
        return RegenOutcome(
            ok=False,
            detail="director produced no shot list — frozen metadata untouched",
            long_words=len(long_script.split()),
            short_words=len(short_script.split()),
        )

    outcome = RegenOutcome(
        ok=True,
        detail=(
            f"long={len(long_script.split())}w short={len(short_script.split())}w "
            f"shots={long_shots}+{short_shots}"
            + ("" if apply else " (dry run — nothing written)")
        ),
        long_words=len(long_script.split()),
        short_words=len(short_script.split()),
        long_shots=long_shots,
        short_shots=short_shots,
        long_script=long_script,
        short_script=short_script,
    )
    if not apply:
        return outcome

    await pool.execute(_WRITEBACK_SQL, ver["id"], json.dumps(new_meta))
    deleted = await pool.execute(_HEAL_ASSETS_SQL, task_id)
    await pool.execute(_HEAL_APPROVALS_SQL, task_id)
    await pool.execute(_HEAL_MARKER_SQL, task_id)
    logger.info(
        "[MEDIA_REGEN] task %s: metadata updated (version %s), %s, approvals "
        "reset, dispatch marker cleared — re-render queues on the next cycle",
        task_id, ver["version"], deleted,
    )
    return outcome


__all__ = ["RegenOutcome", "regen_video_scripts"]
