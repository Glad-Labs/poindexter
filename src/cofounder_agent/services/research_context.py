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
) -> str | None:
    """Search pgvector for similar published posts. Returns None if unavailable."""
    if not topic or not topic.strip():
        return None

    try:
        from poindexter.memory import MemoryClient

        async with MemoryClient() as mem:
            similar_posts = await mem.find_similar_posts(
                topic, limit=5, min_similarity=0.3,
            )

        if not similar_posts:
            return None

        lines: list[str] = [
            "RELATED POSTS WE'VE PUBLISHED "
            "(reference for internal linking, avoid repeating same angles):",
        ]
        pool = getattr(database_service, "pool", None) if database_service else None
        for i, hit in enumerate(similar_posts, 1):
            post_id = hit.source_id
            similarity = hit.similarity
            title = (hit.metadata or {}).get("title", "Untitled")

            # Try to fetch slug + excerpt from the posts table.
            # source_id is either the post UUID or "post/<uuid>" depending on
            # when the embedding was written; try both shapes.
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
                except Exception:  # noqa: BLE001 — non-critical, metadata title is enough
                    pass

            excerpt_short = (excerpt[:120] + "...") if len(excerpt) > 120 else excerpt
            url = f"/posts/{slug}" if slug else f"(post id: {post_id})"
            lines.append(
                f"{i}. [{title}] -- {excerpt_short} ({url}) "
                f"[similarity: {similarity:.2f}]"
            )

        return "\n".join(lines)

    except Exception as e:  # noqa: BLE001 — RAG context is best-effort
        logger.debug("RAG context build failed (non-fatal): %s", e)
        return None
