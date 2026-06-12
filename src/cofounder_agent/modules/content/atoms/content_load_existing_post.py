"""content.load_existing_post — hydrate PipelineState from an existing posts row.

The seo_refresh graph's entry atom. Unlike canonical_blog's generate_draft,
this atom does NOT call an LLM — it reads the already-published post (body
carried unchanged) plus its seo_opportunities row (target query + baseline
metrics) so the downstream seo.optimize_metadata atom can re-optimize the
title/meta toward the query. Read-only; mutates nothing.

Issue: Glad-Labs/poindexter#763 (epic #762).
"""
from __future__ import annotations

import logging
from typing import Any

from plugins.atom import AtomMeta, FieldSpec, RetryPolicy

logger = logging.getLogger(__name__)

ATOM_META = AtomMeta(
    name="content.load_existing_post",
    type="atom",
    version="1.0.0",
    description=(
        "Hydrate pipeline state from an existing posts row (+ its "
        "seo_opportunities row) for the seo_refresh graph. No LLM, no writes."
    ),
    inputs=(
        FieldSpec(name="post_id", type="str", description="posts.id (uuid) to refresh"),
        FieldSpec(name="database_service", type="object", description="DB service"),
    ),
    outputs=(
        FieldSpec(name="content", type="str", description="existing body (carried unchanged)"),
        FieldSpec(name="title", type="str", description="current post title"),
        FieldSpec(name="topic", type="str", description="optimizer fallback keyword source"),
        FieldSpec(name="post_slug", type="str", description="post URL slug"),
        FieldSpec(name="seo_title", type="str", description="current seo_title"),
        FieldSpec(name="seo_description", type="str", description="current seo_description"),
        FieldSpec(name="seo_keywords", type="str", description="current seo_keywords csv"),
        FieldSpec(name="target_query", type="str", description="GSC query to optimize toward"),
        FieldSpec(name="seo_opportunity_id", type="str", description="seo_opportunities.id"),
        FieldSpec(name="tags", type="list", description="post tags (slugs/ids)"),
    ),
    requires=("post_id",),
    produces=(
        "content", "title", "topic", "post_slug", "seo_title", "seo_description",
        "seo_keywords", "target_query", "seo_opportunity_id", "tags",
    ),
    capability_tier=None,
    cost_class="free",
    idempotent=True,
    side_effects=(),
    retry=RetryPolicy(max_attempts=1),
    parallelizable=False,
)

_POST_SQL = """
SELECT id, title, slug, content, seo_title, seo_description, seo_keywords, tag_ids
  FROM posts WHERE id = $1::uuid
"""
_OPP_SQL = """
SELECT id, target_query, current_position, ctr
  FROM seo_opportunities
 WHERE post_id = $1::uuid
 ORDER BY gap_score DESC
 LIMIT 1
"""


async def run(state: dict[str, Any]) -> dict[str, Any]:
    post_id = state.get("post_id")
    pool = getattr(state.get("database_service"), "pool", None)
    if not post_id or pool is None:
        raise RuntimeError(
            "content.load_existing_post: post_id + database_service are required "
            f"(post_id={post_id!r}, pool={'set' if pool else 'None'})"
        )

    async with pool.acquire() as conn:
        post = await conn.fetchrow(_POST_SQL, str(post_id))
        if post is None:
            raise RuntimeError(f"content.load_existing_post: no posts row id={post_id!r}")
        opp = await conn.fetchrow(_OPP_SQL, str(post_id))

    title = post["title"] or ""
    out: dict[str, Any] = {
        "content": post["content"] or "",   # carried verbatim — meta_only never edits body
        "title": title,
        "topic": title,                     # optimizer keyword fallback when target_query == ''
        "post_slug": post["slug"] or "",
        "seo_title": post["seo_title"] or "",
        "seo_description": post["seo_description"] or "",
        "seo_keywords": post["seo_keywords"] or "",
        "tags": list(post["tag_ids"] or []),
        "target_query": (opp["target_query"] if opp else "") or "",
        "seo_opportunity_id": str(opp["id"]) if opp else "",
    }
    logger.info(
        "[content.load_existing_post] hydrated post %s (slug=%s, target_query=%r)",
        post_id, out["post_slug"], out["target_query"],
    )
    return out


__all__ = ["ATOM_META", "run"]
