"""Re-seed canonical_blog graph_def with the caption_images node (version 3).

Adds ``stage.caption_images`` between ``source_featured_image`` and the qa.*
rail block so inline + featured image alt text is re-captioned with vision
(qwen3-vl) at generation time. Forward-only reseed — updates the existing
active ``pipeline_templates`` row from the authoritative spec.

Imports only ``canonical_blog_spec`` (pure data — no LangGraph) + stdlib so
it stays light for the migrations-smoke env.
"""
from __future__ import annotations

import json
import logging

from services.canonical_blog_spec import CANONICAL_BLOG_GRAPH_DEF

logger = logging.getLogger(__name__)


async def up(pool) -> None:
    payload = json.dumps(CANONICAL_BLOG_GRAPH_DEF)
    async with pool.acquire() as conn:
        await conn.execute(
            """
            UPDATE pipeline_templates
               SET graph_def = $1::jsonb, version = 3, updated_at = NOW()
             WHERE slug = 'canonical_blog'
            """,
            payload,
        )
    logger.info("reseed_canonical_blog_caption_images: applied (graph_def v3)")


async def down(pool) -> None:
    logger.info(
        "reseed_canonical_blog_caption_images: no-op down (forward-only reseed)"
    )
