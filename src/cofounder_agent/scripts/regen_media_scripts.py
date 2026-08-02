"""One-off: regenerate a task's frozen media scripts + shot lists in place.

2026-08-01 script-quality remediation. Stage-1 scripts freeze into
``pipeline_versions.stage_data['task_metadata']`` and are re-read at every
Stage-2 render — so a defective frozen script (a "$ trillion" with the number
eaten by the pre-#2876 filename regex, a short that is literally ``**END**``,
meta-commentary read aloud) can never self-heal through re-renders. This
re-runs ``generate_media_scripts`` → ``generate_video_shot_list`` →
``review_video_shot_list`` against the task's published content and writes the
refreshed VIDEO keys back into the frozen metadata:

    video_long_script, short_summary_script, video_scenes,
    video_shot_list, short_shot_list

**Deliberately untouched:** ``podcast_script`` / podcast audio — the podcast
episodes for these tasks are already published artifacts; regenerating their
scripts would desync the frozen text from the shipped audio. Podcast TTS and
the ambient-bed generation are gated OFF for the run (the stage would
otherwise synthesize a fresh episode as a side effect).

With ``--apply`` it also heals the render loop per task: deletes the task's
video/video_short ``media_assets`` rows, resets its ``media_approvals`` to
pending, and clears ``media_pipeline_dispatched_at`` so the next
``dispatch_media_pipeline`` cycle re-renders from the fresh scripts. Without
``--apply`` it prints the regenerated scripts and writes nothing.

Run INSIDE the worker container (needs the app env, DB, and Ollama):

    docker exec poindexter-worker python scripts/regen_media_scripts.py \
        <task_id> [<task_id> ...] [--apply]
"""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
import sys
from typing import Any
from unittest.mock import patch

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger("regen_media_scripts")

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


async def _regen_one(
    task_id: str, *, pool: Any, site_config: Any, platform: Any, apply: bool,
) -> bool:
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
        logger.error("task %s: no post resolves via pipeline_task_id — skipping", task_id)
        return False
    ver = await pool.fetchrow(_LATEST_VERSION_SQL, task_id)
    if not ver:
        logger.error("task %s: no pipeline_versions row — skipping", task_id)
        return False

    # Minimal stage context — mirrors what content_router_service seeds for
    # these stages: content/title (+ seo_title for _resolve_media_title's
    # clean-title preference), the DI seams, and the task id.
    class _DB:
        def __init__(self, p: Any) -> None:
            self.pool = p

    context: dict[str, Any] = {
        "task_id": task_id,
        "title": row["title"] or "",
        "seo_title": row["seo_title"] or "",
        "content": row["content"] or "",
        "site_config": site_config,
        "database_service": _DB(pool),
        "platform": platform,
    }

    # Podcast TTS + ambient generation OFF for the regen — the episode is a
    # published artifact; this run must only produce text + shot lists.
    import modules.content.stages.generate_media_scripts as gms

    with patch.object(gms, "is_tts_enabled", lambda _sc: False), \
         patch.object(gms, "is_audio_gen_enabled", lambda _sc: False):
        scripts_result = await GenerateMediaScriptsStage().execute(context, {})
    updates = dict(scripts_result.context_updates or {})
    long_script = (updates.get("video_long_script") or "").strip()
    short_script = (updates.get("short_summary_script") or "").strip()
    if not long_script:
        logger.error(
            "task %s: regen produced no video_long_script — leaving frozen "
            "metadata untouched", task_id,
        )
        return False
    # Thread the fresh scripts to the director + review (they read context).
    context.update(
        {k: v for k, v in updates.items() if k != "stages"},
    )

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
    plan = (new_meta["video_shot_list"] or {}).get("total_duration_s")
    logger.info(
        "task %s: long=%dw short=%dw shots=%s plan=%ss short_shots=%s",
        task_id, len(long_script.split()), len(short_script.split()),
        len((new_meta["video_shot_list"] or {}).get("shots", []) or []), plan,
        len((new_meta["short_shot_list"] or {}).get("shots", []) or []),
    )
    if not new_meta["video_shot_list"]:
        logger.error(
            "task %s: director produced no shot list — leaving frozen "
            "metadata untouched", task_id,
        )
        return False

    if not apply:
        print(f"\n===== {task_id} — LONG (dry run) =====\n{long_script}\n")
        print(f"===== {task_id} — SHORT (dry run) =====\n{short_script or '(no short)'}\n")
        return True

    await pool.execute(_WRITEBACK_SQL, ver["id"], json.dumps(new_meta))
    deleted = await pool.execute(_HEAL_ASSETS_SQL, task_id)
    await pool.execute(_HEAL_APPROVALS_SQL, task_id)
    await pool.execute(_HEAL_MARKER_SQL, task_id)
    logger.info(
        "task %s: metadata updated (version %s), %s, approvals reset, "
        "dispatch marker cleared — re-render queues on the next cycle",
        task_id, ver["version"], deleted,
    )
    return True


async def _main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("task_ids", nargs="+")
    parser.add_argument("--apply", action="store_true", help="write back + heal (default: dry run)")
    args = parser.parse_args()

    import asyncpg
    from brain.bootstrap import resolve_database_url

    from services.di_wiring import (
        build_and_wire_subprocess_with_container,
        build_platform_for_subprocess,
    )

    pool = await asyncpg.create_pool(resolve_database_url(), min_size=1, max_size=3)
    try:
        site_config, _container = await build_and_wire_subprocess_with_container(pool)
        platform = build_platform_for_subprocess(pool, site_config)
        ok = True
        for task_id in args.task_ids:
            ok = await _regen_one(
                task_id, pool=pool, site_config=site_config,
                platform=platform, apply=args.apply,
            ) and ok
        return 0 if ok else 1
    finally:
        await pool.close()


if __name__ == "__main__":
    sys.exit(asyncio.run(_main()))
