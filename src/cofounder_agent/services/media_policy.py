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
