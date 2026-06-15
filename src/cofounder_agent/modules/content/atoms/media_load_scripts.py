"""media.load_scripts — Stage-2 entry atom (epic poindexter#689).

Loads the persisted Stage-1 media artifacts (scripts + shot-lists) for a task
so the downstream ``media_pipeline`` render/QA/gate nodes have them in graph
state. The source of truth is ``pipeline_versions.stage_data['task_metadata']``
(written by ``content.persist_task`` at Stage-1 finalize) — NOT
``posts.metadata``, which only carries the post-level seam (``pipeline_task_id``).

Reading from the persisted artifacts (instead of re-deriving from post content)
is the spine of the redesign: a re-render reuses the writer/director's
pipeline-time creative work rather than re-inventing prompts (root fix for
#674/#675).

Produces the six Stage-1 channels declared on ``PipelineState`` (#1226):
``podcast_script``, ``video_scenes``, ``short_summary_script``,
``video_shot_list``, ``short_shot_list`` (#517), and
``video_ambient_audio_path``. (The podcast-audio paths from #1233 / #690
join once podcast rendering lands in a later plan.)
"""

from __future__ import annotations

import json
import logging
from typing import Any

from plugins.atom import AtomMeta, FieldSpec, RetryPolicy

logger = logging.getLogger(__name__)

ATOM_META = AtomMeta(
    name="media.load_scripts",
    type="atom",
    version="1.0.0",
    description=(
        "Stage-2 entry: load persisted Stage-1 scripts/shot-lists from "
        "pipeline_versions.task_metadata into media_pipeline graph state."
    ),
    inputs=(
        FieldSpec(name="task_id", type="str", description="pipeline task id"),
        FieldSpec(
            name="database_service", type="object",
            description="DB service (pool source)", required=False,
        ),
    ),
    outputs=(
        FieldSpec(name="podcast_script", type="str", description="podcast VO script"),
        FieldSpec(name="video_long_script", type="str", description="long-form video narration script"),
        FieldSpec(name="video_scenes", type="list", description="long-form scene prompts"),
        FieldSpec(name="short_summary_script", type="str", description="short-form narration"),
        FieldSpec(name="video_shot_list", type="dict", description="director shot list"),
        FieldSpec(name="short_shot_list", type="dict", description="short-form (9:16) director shot list"),
        FieldSpec(name="video_ambient_audio_path", type="str", description="ambient bed path"),
    ),
    requires=("task_id",),
    produces=(
        "podcast_script",
        "video_long_script",
        "video_scenes",
        "short_summary_script",
        "video_shot_list",
        "short_shot_list",
        "video_ambient_audio_path",
    ),
    capability_tier=None,
    cost_class="free",
    idempotent=True,
    side_effects=(),
    retry=RetryPolicy(
        max_attempts=3,
        backoff_s=1.0,
        retry_on=("asyncpg.PostgresConnectionError",),
    ),
    parallelizable=False,
)

# Defaults surfaced when a task has no persisted media artifacts (e.g. a post
# created before Stage-1 persistence, or a non-media task). The render nodes
# downstream no-op gracefully on empty inputs.
_EMPTY = {
    "podcast_script": "",
    "video_long_script": "",
    "video_scenes": [],
    "short_summary_script": "",
    "video_shot_list": None,
    "short_shot_list": None,
    "video_ambient_audio_path": "",
}


async def run(state: dict[str, Any]) -> dict[str, Any]:
    """Load Stage-1 media artifacts for ``state['task_id']`` from the latest
    ``pipeline_versions`` row's ``task_metadata``."""
    task_id = state.get("task_id")
    database_service = state.get("database_service")
    pool = (
        getattr(database_service, "pool", None)
        if database_service is not None
        else state.get("pool")
    )
    if not task_id or pool is None:
        raise ValueError("media.load_scripts requires task_id and a DB pool")

    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            "SELECT stage_data -> 'task_metadata' AS task_metadata "
            "FROM pipeline_versions WHERE task_id = $1 "
            "ORDER BY version DESC LIMIT 1",
            task_id,
        )

    meta: dict[str, Any] = {}
    raw = row["task_metadata"] if row else None
    if raw is not None:
        # asyncpg returns jsonb as str unless a codec is registered — handle
        # both (same defensive parse as load_active_graph_def).
        if isinstance(raw, str):
            try:
                raw = json.loads(raw)
            except json.JSONDecodeError:
                logger.warning(
                    "[media.load_scripts] task_metadata for %s is not valid JSON",
                    task_id,
                )
                raw = None
        if isinstance(raw, dict):
            meta = raw

    result = {
        "podcast_script": meta.get("podcast_script", _EMPTY["podcast_script"]),
        "video_long_script": meta.get("video_long_script", _EMPTY["video_long_script"]),
        "video_scenes": meta.get("video_scenes", _EMPTY["video_scenes"]),
        "short_summary_script": meta.get(
            "short_summary_script", _EMPTY["short_summary_script"],
        ),
        "video_shot_list": meta.get("video_shot_list", _EMPTY["video_shot_list"]),
        "short_shot_list": meta.get("short_shot_list", _EMPTY["short_shot_list"]),
        "video_ambient_audio_path": meta.get(
            "video_ambient_audio_path", _EMPTY["video_ambient_audio_path"],
        ),
    }
    logger.info(
        "[media.load_scripts] task=%s loaded: podcast=%dc scenes=%d shot_list=%s",
        task_id,
        len(result["podcast_script"] or ""),
        len(result["video_scenes"] or []),
        "yes" if result["video_shot_list"] else "no",
    )
    return result


__all__ = ["ATOM_META", "run"]
