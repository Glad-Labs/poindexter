"""podcast.persist — Stage-3 terminal atom (#689 deviation, ``podcast_pipeline``).

Moves the rendered narration MP3 out of the temp dir into the durable podcast
dir (``~/.poindexter/podcast``) and records a **task-keyed** ``media_assets``
row (``type='podcast'``, ``post_id=NULL``). The post is resolved later by the
``podcast_distribute`` job via ``posts.metadata->>'pipeline_task_id'`` (mirrors
``media.persist``'s task-keyed/post-deferred split for video).

Best-effort: a missing render or a no-pool environment never raises — the node
returns the list of recorded asset ids and lets the graph finish.
"""

from __future__ import annotations

import logging
import os
import shutil
from typing import Any

from plugins.atom import AtomMeta, FieldSpec, RetryPolicy
from services.media_asset_recorder import record_media_asset
from services.podcast_service import PODCAST_DIR
from utils.findings import emit_finding

logger = logging.getLogger(__name__)

ATOM_META = AtomMeta(
    name="podcast.persist",
    type="atom",
    version="1.0.0",
    description=(
        "Move the rendered podcast narration MP3 out of temp into the durable "
        "podcast dir and record a task-keyed media_assets row (type='podcast'; "
        "post resolved later at distribution)."
    ),
    inputs=(
        FieldSpec(name="task_id", type="str", description="pipeline task id"),
        FieldSpec(
            name="podcast_audio_path", type="str",
            description="temp path of the rendered narration MP3", required=False,
        ),
        FieldSpec(
            name="database_service", type="object",
            description="DB service (pool seam)", required=False,
        ),
    ),
    outputs=(
        FieldSpec(
            name="media_assets_recorded", type="list",
            description="ids of the media_assets rows written",
        ),
    ),
    requires=("task_id",),
    produces=("media_assets_recorded",),
    capability_tier=None,
    cost_class="free",
    idempotent=False,
    side_effects=("db_write", "file_move"),
    retry=RetryPolicy(),
    parallelizable=False,
)


def _duration_ms(path: str) -> int | None:
    """Best-effort audio duration via ffprobe; ``None`` when unavailable.

    Duration is metadata for the RSS ``<itunes:duration>`` tag — never
    load-bearing, so any probe failure (ffprobe absent, bad file) returns None.
    """
    try:
        import subprocess

        out = subprocess.run(
            [
                "ffprobe", "-v", "error", "-show_entries", "format=duration",
                "-of", "default=noprint_wrappers=1:nokey=1", path,
            ],
            capture_output=True, text=True, timeout=30,
        )
        raw = out.stdout.strip()
        return int(float(raw) * 1000) if raw else None
    except Exception:  # noqa: BLE001 — duration is metadata, never load-bearing
        return None


async def run(state: dict[str, Any]) -> dict[str, Any]:
    """Move the temp render to durable storage + record a podcast media_assets row."""
    task_id = state.get("task_id")
    if not task_id:
        raise ValueError("podcast.persist requires task_id")

    database_service = state.get("database_service")
    pool = (
        getattr(database_service, "pool", None)
        if database_service is not None
        else state.get("pool")
    )
    if pool is None:
        logger.warning(
            "[podcast.persist] no DB pool for task %s — skipping persist", task_id,
        )
        return {"media_assets_recorded": []}

    src = state.get("podcast_audio_path") or ""
    if not src or not os.path.exists(src):
        # The render atom emits its own finding on failure; an absent path here
        # just means no narration was produced this run.
        return {"media_assets_recorded": []}

    durable = PODCAST_DIR / f"{task_id}.mp3"
    try:
        PODCAST_DIR.mkdir(parents=True, exist_ok=True)
        shutil.move(src, durable)
    except OSError as exc:
        # NOT the no-op above: the TTS already succeeded and the audio exists —
        # only the move to durable storage failed. Returning the same empty list
        # records no media_asset, so media_reconciliation sees a post with no
        # podcast and re-dispatches, re-paying the whole TTS render against the
        # identical disk failure. A warning alone is visible to a human reading
        # Loki but leaves the return value success-shaped, which is exactly how
        # poindexter#874 wedged a task for three weeks. Emit (issue #877).
        logger.warning("[podcast.persist] move failed for %s: %s", src, exc)
        emit_finding(
            source="podcast.persist",
            kind="podcast_persist_failed",
            title="podcast: rendered audio could not be moved to durable storage",
            body=(
                f"The podcast audio for task {task_id} rendered successfully but "
                f"could not be moved from {src} to {durable}: {exc}. No "
                f"media_asset row was written, so the podcast does not exist as "
                f"far as the DB is concerned and media_reconciliation will "
                f"re-dispatch it — re-paying the TTS render — until the "
                f"underlying disk/permission problem is fixed."
            ),
            severity="warn",
            dedup_key=f"podcast_persist_failed:{task_id}",
            extra={
                "task_id": str(task_id or ""),
                "src": str(src),
                "durable": str(durable),
                "error": str(exc),
            },
        )
        return {"media_assets_recorded": []}

    try:
        size = os.path.getsize(durable)
    except OSError:
        size = 0

    asset_id = await record_media_asset(
        pool=pool,
        post_id=None,  # resolved later at distribution (podcast_distribute)
        task_id=task_id,
        asset_type="podcast",
        storage_path=str(durable),
        storage_provider="local",
        source="pipeline",
        provider_plugin="tts.kokoro",
        mime_type="audio/mpeg",
        duration_ms=_duration_ms(str(durable)),
        file_size_bytes=size,
    )

    recorded: list[str] = []
    if asset_id:
        recorded.append(asset_id)
        logger.info(
            "[podcast.persist] recorded podcast media_asset %s for task %s (%s)",
            asset_id, task_id, durable,
        )
    return {"media_assets_recorded": recorded}


__all__ = ["ATOM_META", "run"]
