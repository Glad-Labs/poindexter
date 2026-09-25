"""Give already-published long videos their composed thumbnail.

The render step (``media.render_thumbnail``) covers every video rendered from
2026-09-25 on. The videos already on the channel were uploaded with
YouTube's own frame, and a thumbnail can be set on a published video, so this
closes that gap.

Two passes, so what is uploaded is exactly what the operator looked at:

- **Dry run** (default) composes a thumbnail for each published long video
  that has none and stores it as its ``video_thumbnail`` asset. That is a
  LOCAL write; nothing leaves the machine. It reports every path to review.
- **Apply** uploads the stored thumbnail of each video whose thumbnail is not
  yet set on YouTube. It never recomposes, because a second model call could
  write different text from what was reviewed. A video with no stored
  thumbnail is composed and uploaded in one go, and the operator's
  ``--apply`` is the approval.

``thumbnails.set`` needs only the upload scope. A 403 is reported with the
channel-verification fix (``describe_thumbnail_error``).
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass
from typing import Any

from poindexter.services.logger_config import get_logger

logger = get_logger(__name__)

_PUBLISHED_LONG_SQL = """
SELECT ma.task_id::text AS task_id,
       ma.storage_path AS video_path,
       ma.platform_video_ids->>'youtube' AS video_id,
       p.id::text AS post_id, p.slug, p.title,
       pt.niche_slug,
       th.storage_path AS thumbnail_path,
       th.metadata->'youtube'->>'status' AS thumbnail_status
  FROM media_assets ma
  LEFT JOIN posts p ON p.metadata->>'pipeline_task_id' = ma.task_id::text
  LEFT JOIN pipeline_tasks pt ON pt.task_id::text = ma.task_id::text
  LEFT JOIN LATERAL (
        SELECT t.storage_path, t.metadata FROM media_assets t
         WHERE t.task_id = ma.task_id AND t.type = 'video_thumbnail'
         ORDER BY t.created_at DESC LIMIT 1
  ) th ON true
 WHERE ma.type = 'video'
   AND COALESCE(ma.platform_video_ids->>'youtube', '') <> ''
 ORDER BY ma.created_at DESC
"""

_TASK_CONTEXT_SQL = """
SELECT stage_data->'task_metadata'->'video_shot_list' AS shot_list,
       stage_data->'task_metadata'->>'video_long_script' AS script
  FROM pipeline_versions
 WHERE task_id::text = $1
 ORDER BY version DESC LIMIT 1
"""


@dataclass
class ThumbnailOutcome:
    video_id: str
    title: str
    action: str
    path: str = ""
    hook: str = ""
    background: str = ""
    error: str = ""

    @property
    def failed(self) -> bool:
        return bool(self.error)


def _matches(row: dict[str, Any], selector: str | None) -> bool:
    if not selector:
        return True
    return selector in (row.get("post_id"), row.get("task_id"), row.get("video_id"), row.get("slug"))


async def _compose_and_store(pool: Any, site_config: Any, row: dict[str, Any]) -> tuple[str, dict[str, Any], str]:
    """Compose this video's thumbnail and store it as its asset. ``(path, meta, error)``."""
    from poindexter.services.video_service import VIDEO_DIR
    from poindexter.services.video_thumbnail import compose_video_thumbnail, store_thumbnail_asset

    task_id = row["task_id"]
    shot_list: Any = None
    script = ""
    try:
        ctx = await pool.fetchrow(_TASK_CONTEXT_SQL, task_id)
        if ctx is not None:
            shot_list = ctx["shot_list"]
            if isinstance(shot_list, str):
                shot_list = json.loads(shot_list)
            script = str(ctx["script"] or "")
    except Exception as exc:  # noqa: BLE001 — context only sharpens the result
        logger.warning(
            "[thumbnail_backfill] render context lookup failed for %s (%s) — composing "
            "without the shot list and narration", task_id, exc,
        )
    result = await compose_video_thumbnail(
        task_id=task_id, pool=pool, site_config=site_config,
        video_path=str(row.get("video_path") or ""),
        shot_list=shot_list if isinstance(shot_list, dict) else None,
        source_text=script, niche_slug=row.get("niche_slug") or None,
    )
    if result is None:
        return "", {}, "compose failed (video_thumbnail_enabled off, or chromium returned nothing)"
    meta = {
        "hook": result.hook, "hook_note": result.hook_note,
        "background": result.background, "size_bytes": result.size_bytes,
        "composed_by": "youtube thumbnails backfill",
    }
    asset_id = await store_thumbnail_asset(
        pool, task_id=task_id, src_path=result.path, meta=meta,
        post_id=row.get("post_id") or None, video_dir=VIDEO_DIR, video_recorded_now=True,
    )
    if not asset_id:
        return "", meta, "could not store the thumbnail asset"
    return str(VIDEO_DIR / f"{task_id}_thumbnail.jpg"), meta, ""


async def _stamp(pool: Any, task_id: str, video_id: str, status: str) -> None:
    import time

    stamp = {"video_id": video_id, "status": status[:400], "at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())}
    await pool.execute(
        "UPDATE media_assets SET metadata = COALESCE(metadata, '{}'::jsonb) "
        "|| jsonb_build_object('youtube', $2::jsonb) "
        "WHERE task_id::text = $1 AND type = 'video_thumbnail'",
        task_id, json.dumps(stamp),
    )


async def backfill_youtube_thumbnails(
    pool: Any,
    site_config: Any,
    *,
    selector: str | None = None,
    apply: bool = False,
    recompose: bool = False,
    limit: int | None = None,
) -> list[ThumbnailOutcome]:
    """Compose (dry run) or upload (apply) thumbnails for published long videos.

    ``recompose`` replaces a stored thumbnail with a fresh one (dry run only;
    apply always uploads what is stored).
    """
    rows = [dict(r) for r in await pool.fetch(_PUBLISHED_LONG_SQL)]
    rows = [r for r in rows if _matches(r, selector)]
    if limit is not None:
        rows = rows[: max(0, limit)]
    adapter = None
    if apply:
        from poindexter.services.publish_adapters.youtube import YouTubePublishAdapter

        adapter = YouTubePublishAdapter(site_config=site_config)

    outcomes: list[ThumbnailOutcome] = []
    for row in rows:
        video_id, title = str(row["video_id"]), str(row.get("title") or row["task_id"])
        path = str(row.get("thumbnail_path") or "")
        status = str(row.get("thumbnail_status") or "")
        have = bool(path) and os.path.exists(path)

        if not apply:
            if have and not recompose:
                outcomes.append(ThumbnailOutcome(video_id, title, f"stored ({status or 'not uploaded'})", path))
                continue
            new_path, meta, error = await _compose_and_store(pool, site_config, row)
            outcomes.append(ThumbnailOutcome(
                video_id, title, "composed — review, then --apply" if not error else "compose failed",
                new_path, meta.get("hook", ""), meta.get("background", ""), error,
            ))
            continue

        if status == "set":
            outcomes.append(ThumbnailOutcome(video_id, title, "already set", path))
            continue
        meta: dict[str, Any] = {}
        if not have:
            path, meta, error = await _compose_and_store(pool, site_config, row)
            if error:
                outcomes.append(ThumbnailOutcome(video_id, title, "compose failed", error=error))
                continue
        ok, detail = await adapter.set_thumbnail(video_id=video_id, thumbnail_path=path)
        try:
            await _stamp(pool, row["task_id"], video_id, "set" if ok else f"failed: {detail}")
        except Exception as exc:  # noqa: BLE001 — the upload already happened; report, don't undo
            logger.warning("[thumbnail_backfill] stamp failed for %s: %s", video_id, exc)
        outcomes.append(ThumbnailOutcome(
            video_id, title, "uploaded" if ok else "upload failed", path,
            meta.get("hook", ""), meta.get("background", ""), "" if ok else detail,
        ))
    return outcomes


__all__ = ["ThumbnailOutcome", "backfill_youtube_thumbnails"]
