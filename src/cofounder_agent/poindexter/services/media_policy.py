"""Resolve a post's media plan from its niche, callable at approval time.

``niches.default_media_to_generate`` (migration 20260519_134736) is the
canonical seam for which media a niche opts into. Historically this lookup
lived inline in ``publish_service.publish_post_from_task`` (resolved at
publish). Exposing it as a standalone callable lets the per-medium gate
engine build its gate sequence at draft-*approval* time, so the operator
reviews media before the post goes live (Glad-Labs/poindexter#24).

Falls back to an empty list when the niche is missing or opts into no
media — safer than defaulting to "spawn everything" for an unknown niche
(post-mortem from glad-labs-stack#480/#481).
"""
from __future__ import annotations

from typing import Any


async def resolve_media_to_generate(pool: Any, niche_slug: str | None) -> list[str]:
    """Return the media a niche opts into, or ``[]`` when none/unknown."""
    if not niche_slug:
        return []
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            "SELECT default_media_to_generate FROM niches WHERE slug = $1",
            niche_slug,
        )
    if not row or not row["default_media_to_generate"]:
        return []
    return list(row["default_media_to_generate"])


def wants_long_video(media_to_generate: list[str] | None) -> bool:
    """Whether a media plan includes the 16:9 long-form video.

    False only when the plan names the Short and not the long form, which is
    how a niche turns long videos off while keeping Shorts (2026-10-27: long
    videos averaged ~5 views against ~30 for a Short). An empty or unknown
    plan keeps the long form, the behaviour before this existed.
    """
    plan = set(media_to_generate or ())
    return not ("video_short" in plan and "video" not in plan)


def wants_long_video_sql(task_id_expr: str) -> str:
    """SQL boolean: the post made from task ``task_id_expr`` wants a long video.

    The same rule as :func:`wants_long_video`, read off the post's own
    ``media_to_generate`` (stamped from the niche at publish). A task with no
    post row yet, or a post with no plan, wants it, as before. Shared by the
    media dispatcher, its stranded metric and the shot-list backfill so the
    three cannot disagree about which lane a post is owed.
    """
    return (
        "COALESCE((SELECT NOT ("  # nosec B608 - task_id_expr is a column name from our own SQL, never input
        "COALESCE('video_short' = ANY(p.media_to_generate), FALSE) "
        "AND NOT COALESCE('video' = ANY(p.media_to_generate), FALSE)) "
        "FROM posts p WHERE p.metadata->>'pipeline_task_id' = " + task_id_expr + " "
        "ORDER BY p.created_at DESC LIMIT 1), TRUE)"
    )


def shot_count_sql(version_alias: str, lane_key: str) -> str:
    """SQL int: shots in ``<version_alias>.stage_data.task_metadata.<lane_key>``."""
    path = f"{version_alias}.stage_data -> 'task_metadata' -> '{lane_key}' -> 'shots'"
    return (
        f"CASE WHEN jsonb_typeof({path}) = 'array' "
        f"THEN jsonb_array_length({path}) ELSE 0 END"
    )
