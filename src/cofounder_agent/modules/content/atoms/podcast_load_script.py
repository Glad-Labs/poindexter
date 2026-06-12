"""podcast.load_script — Stage-3 entry atom (#689 deviation, ``podcast_pipeline``).

Loads the persisted Stage-1 podcast artifacts for ``state['task_id']`` from the
latest ``pipeline_versions`` row's ``task_metadata`` — the ``podcast_script``
and the ``podcast_intro_audio_path`` sting (both persisted by #690/#1233). The
downstream ``podcast.render`` atom no-ops gracefully on an empty script.

Mirrors ``media.load_scripts`` (same persisted source of truth, same defensive
jsonb-as-str parse) but scoped to the podcast-only channels so the Stage-3 graph
shares nothing with the video render lane.
"""

from __future__ import annotations

import json
import logging
from typing import Any

from plugins.atom import AtomMeta, FieldSpec, RetryPolicy

logger = logging.getLogger(__name__)

ATOM_META = AtomMeta(
    name="podcast.load_script",
    type="atom",
    version="1.0.0",
    description=(
        "Stage-3 entry: load the persisted podcast_script + intro-sting path "
        "from pipeline_versions.task_metadata into podcast_pipeline graph state."
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
        FieldSpec(
            name="podcast_intro_audio_path", type="str",
            description="intro-sting audio path (#690)",
        ),
    ),
    requires=("task_id",),
    produces=("podcast_script", "podcast_intro_audio_path"),
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

_EMPTY = {"podcast_script": "", "podcast_intro_audio_path": ""}


async def run(state: dict[str, Any]) -> dict[str, Any]:
    """Load the podcast script + intro path for ``state['task_id']``."""
    task_id = state.get("task_id")
    database_service = state.get("database_service")
    pool = (
        getattr(database_service, "pool", None)
        if database_service is not None
        else state.get("pool")
    )
    if not task_id or pool is None:
        raise ValueError("podcast.load_script requires task_id and a DB pool")

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
        # asyncpg returns jsonb as str unless a codec is registered — handle both.
        if isinstance(raw, str):
            try:
                raw = json.loads(raw)
            except json.JSONDecodeError:
                logger.warning(
                    "[podcast.load_script] task_metadata for %s is not valid JSON",
                    task_id,
                )
                raw = None
        if isinstance(raw, dict):
            meta = raw

    result = {
        "podcast_script": meta.get("podcast_script", _EMPTY["podcast_script"]),
        "podcast_intro_audio_path": meta.get(
            "podcast_intro_audio_path", _EMPTY["podcast_intro_audio_path"],
        ),
    }
    logger.info(
        "[podcast.load_script] task=%s loaded: podcast_script=%dc intro=%s",
        task_id,
        len(result["podcast_script"] or ""),
        "yes" if result["podcast_intro_audio_path"] else "no",
    )
    return result


__all__ = ["ATOM_META", "run"]
