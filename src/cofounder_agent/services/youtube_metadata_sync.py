"""Re-push YouTube title/description/tags for videos already on the channel.

The upload path composes metadata once, at upload time, and never revisits it.
So when the composition rules change, every video already on the channel keeps
whatever the old rules produced — which is exactly what happened on 2026-08-31:
the description builder stopped dumping the whole article body, and the 12
videos uploaded before that kept their 4,800-character markdown wall.

This module closes that gap. It recomposes metadata from the post row using
**the same builders the upload path uses** (``services.jobs.youtube_payload``),
so a future change to the composition rules reaches new and old videos through
one code path rather than two that can disagree.

Scope note: ``videos.update`` needs the ``youtube.force-ssl`` scope, and the
original consent was ``youtube.upload`` (insert-only). Until the operator
re-consents via ``poindexter integrations youtube setup --with-update`` every
call here fails with that remediation — deliberately, and loudly, rather than
appearing to work.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any

from services.jobs.youtube_payload import (
    _build_youtube_description,
    _build_youtube_title,
    _parse_seo_keywords,
)

logger = logging.getLogger(__name__)

# Videos we uploaded, joined to the post they promote. pipeline_distributions
# is the durable record of what actually landed on the platform (external_id =
# the YouTube video id), and media_assets.platform_video_ids carries the same
# handle — the join keys off the former because it is the row the dispatcher
# writes transactionally with the upload.
_TARGETS_SQL = """
    SELECT pd.external_id AS video_id,
           p.id::text     AS post_id,
           p.title,
           p.excerpt,
           p.content,
           p.seo_keywords,
           p.slug,
           -- Which render this upload was. Without it a re-sync would rebuild
           -- every title as long-form and strip the Short's distinguishing
           -- suffix back off, re-colliding the pair it was added to separate.
           (SELECT ma.type
              FROM media_assets ma
             WHERE ma.post_id = pd.post_id
               AND ma.platform_video_ids->>'youtube' = pd.external_id
             LIMIT 1) AS asset_type
      FROM pipeline_distributions pd
      JOIN posts p ON p.id = pd.post_id
     WHERE pd.target = 'youtube'
       AND pd.status = 'published'
       AND pd.external_id IS NOT NULL
       AND ($1::text IS NULL OR p.id::text = $1 OR pd.external_id = $1)
     ORDER BY pd.created_at DESC
"""


@dataclass(frozen=True)
class SyncOutcome:
    """One video's result. ``applied`` is False for a dry run or a failure."""

    video_id: str
    post_id: str
    title: str
    applied: bool
    error: str | None = None
    description_chars: int = 0
    tag_count: int = 0


async def _load_targets(pool: Any, selector: str | None) -> list[dict[str, Any]]:
    async with pool.acquire() as conn:
        rows = await conn.fetch(_TARGETS_SQL, selector)
    return [dict(r) for r in rows]


def _compose(row: dict[str, Any], site_config: Any) -> tuple[str, str, list[str]]:
    """Recompose (title, description, tags) exactly as an upload would."""
    description = _build_youtube_description(
        seo_description=row.get("excerpt") or "",
        body=row.get("content") or "",
        site_config=site_config,
        slug=row.get("slug") or "",
    )
    tags = _parse_seo_keywords(row.get("seo_keywords") or "")
    title = _build_youtube_title(
        row.get("title") or "",
        shorts=row.get("asset_type") == "video_short",
        site_config=site_config,
    )
    return (title, description, tags)


async def sync_youtube_metadata(
    pool: Any,
    site_config: Any,
    *,
    selector: str | None = None,
    apply: bool = False,
    limit: int | None = None,
) -> list[SyncOutcome]:
    """Recompose and (optionally) push metadata for published YouTube videos.

    ``selector`` narrows to one post id or one video id; ``None`` means every
    published upload. ``apply`` defaults to **False** — this writes to a public
    platform, so the caller has to say so explicitly and a mistake costs a
    printed diff rather than 12 rewritten videos.

    Returns one :class:`SyncOutcome` per video, failures included: a partial
    result is the useful one when a scope error stops the run at the first
    video, and swallowing that would look like "nothing needed changing".
    """
    rows = await _load_targets(pool, selector)
    if limit is not None:
        rows = rows[: max(0, limit)]
    if not rows:
        return []

    adapter = None
    if apply:
        from services.publish_adapters.youtube import YouTubePublishAdapter

        adapter = YouTubePublishAdapter(site_config=site_config)

    outcomes: list[SyncOutcome] = []
    for row in rows:
        title, description, tags = _compose(row, site_config)
        if not apply:
            outcomes.append(
                SyncOutcome(
                    video_id=row["video_id"],
                    post_id=row["post_id"],
                    title=title,
                    applied=False,
                    description_chars=len(description),
                    tag_count=len(tags),
                )
            )
            continue

        result = await adapter.update_metadata(  # type: ignore[union-attr]
            video_id=row["video_id"],
            title=title,
            description=description,
            tags=tags or None,
        )
        ok = bool(getattr(result, "success", False))
        outcomes.append(
            SyncOutcome(
                video_id=row["video_id"],
                post_id=row["post_id"],
                title=title,
                applied=ok,
                error=None if ok else getattr(result, "error", "unknown error"),
                description_chars=len(description),
                tag_count=len(tags),
            )
        )
        if ok:
            logger.info(
                "[YOUTUBE_SYNC] updated %s (post %s): description %d chars, %d tags",
                row["video_id"], row["post_id"][:8], len(description), len(tags),
            )
        else:
            logger.warning(
                "[YOUTUBE_SYNC] update failed for %s: %s",
                row["video_id"], outcomes[-1].error,
            )

    return outcomes


__all__ = ["SyncOutcome", "sync_youtube_metadata"]
