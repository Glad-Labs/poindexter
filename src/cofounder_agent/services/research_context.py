"""Build RAG (retrieval-augmented generation) context for a new draft.

Lifted from content_router_service.py during Phase E2. Searches the
pgvector embedding store for already-published posts similar to the
given topic, then formats them as a reference block the writer can
consult for internal linking + angle diversity.

Migrated 2026-04-11 from direct ``database_service.embeddings.search_similar``
calls to ``poindexter.memory.MemoryClient.find_similar_posts`` per Gitea #192
slice 3. The MemoryClient helper hardcodes ``source_table='posts'`` so the
singular/plural silent-zero-result bug can never recur here.
"""

from __future__ import annotations

import logging
from typing import Any

logger = logging.getLogger(__name__)


async def build_rag_context(
    database_service: Any | None,
    topic: str,
    source_tags: list[str] | None = None,
    source_category: str | None = None,
    *,
    site_config: Any = None,
) -> str | None:
    """Search pgvector for similar published posts. Returns None if unavailable.

    GH-88: when ``source_tags``/``source_category`` are provided, the
    candidates go through :class:`InternalLinkCoherenceFilter` before
    being rendered — rejects off-topic suggestions (e.g. CadQuery pinned
    as "related" to an asyncio post) and caps how many times any single
    target can be recommended across the corpus.
    """
    if not topic or not topic.strip():
        return None

    try:
        from poindexter.memory import MemoryClient

        async with MemoryClient() as mem:
            # Over-fetch so the coherence filter has room to drop off-topic
            # candidates and still return enough relevant ones.
            fetch_limit = 15 if source_tags or source_category else 5
            similar_posts = await mem.find_similar_posts(
                topic, limit=fetch_limit, min_similarity=0.3,
            )

        if not similar_posts:
            return None

        pool = getattr(database_service, "pool", None) if database_service else None

        # Resolve slug + excerpt for every hit up front; we need slug for
        # the coherence filter's target-tag lookup anyway.
        resolved: list[dict[str, Any]] = []
        for hit in similar_posts:
            post_id = hit.source_id
            similarity = hit.similarity
            title = (hit.metadata or {}).get("title", "Untitled")
            slug = ""
            excerpt = ""
            if pool:
                try:
                    lookup_id = post_id.removeprefix("post/")
                    row = await pool.fetchrow(
                        "SELECT slug, excerpt FROM posts WHERE id::text = $1 LIMIT 1",
                        lookup_id,
                    )
                    if row:
                        slug = row.get("slug") or ""
                        excerpt = row.get("excerpt") or ""
                except Exception as e:
                    logger.warning(
                        "[research_context] slug/excerpt lookup for "
                        "post_id=%r failed; result will lack a real "
                        "URL/excerpt: %s",
                        post_id, e,
                    )
            resolved.append({
                "post_id": post_id,
                "similarity": similarity,
                "title": title,
                "slug": slug,
                "excerpt": excerpt,
            })

        # GH-88: apply the coherence filter if we have source context + a DB.
        # source_category is accepted here for call-site symmetry with
        # upstream code, but the filter itself is tag-driven — it is not
        # forwarded to InternalLinkCoherenceFilter.
        if pool and source_tags:
            try:
                from services.internal_link_coherence import (
                    InternalLinkCoherenceFilter,
                    LinkCandidate,
                )
                candidates = [
                    LinkCandidate(
                        slug=r["slug"],
                        title=r["title"],
                        similarity=r["similarity"],
                    )
                    for r in resolved
                    if r["slug"]
                ]
                filt = InternalLinkCoherenceFilter(pool=pool, site_config=site_config)
                kept = await filt.filter_candidates(
                    source_tags=list(source_tags or []),
                    candidates=candidates,
                )
                kept_slugs = {c.slug for c in kept}
                resolved = [r for r in resolved if r["slug"] in kept_slugs]
            except Exception:
                # Escalated from DEBUG — this path was silently failing on
                # every call due to signature mismatches; keep it loud.
                logger.exception("Internal-link coherence filter failed")

        # Trim to 5 after filtering (was the legacy cap).
        resolved = resolved[:5]
        if not resolved:
            return None

        lines: list[str] = [
            "RELATED POSTS WE'VE PUBLISHED "
            "(reference for internal linking, avoid repeating same angles):",
        ]
        for i, r in enumerate(resolved, 1):
            excerpt_short = (
                (r["excerpt"][:120] + "...")
                if len(r["excerpt"]) > 120
                else r["excerpt"]
            )
            url = f"/posts/{r['slug']}" if r["slug"] else f"(post id: {r['post_id']})"
            lines.append(
                f"{i}. [{r['title']}] -- {excerpt_short} ({url}) "
                f"[similarity: {r['similarity']:.2f}]"
            )

        return "\n".join(lines)

    except Exception as e:
        logger.debug("RAG context build failed (non-fatal): %s", e)
        return None
