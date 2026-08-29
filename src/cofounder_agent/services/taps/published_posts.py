"""PostsTap — ingest published posts from the ``posts`` table.

Replaces Phase 2 of ``scripts/auto-embed.py``. Builds embeddable text
from title + excerpt + content, yields one Document per post. Dedup
via ``content_hash`` happens in the runner; this Tap just emits.
"""

from __future__ import annotations

import logging
from collections.abc import AsyncIterator
from typing import Any

from plugins.tap import Document

logger = logging.getLogger(__name__)


def build_post_text(
    *, title: str | None, excerpt: str | None, content: str | None
) -> str:
    """The canonical embeddable text for one post.

    Shared with ``services/embedding_service.py`` on purpose. Both write to
    the SAME natural key — ``('posts', <post id>, chunk_index,
    embedding_model)`` — so if they build the text differently they produce
    different content hashes for the same row and take turns overwriting each
    other on every republish. They used to: ``embed_post`` wrote
    ``title\nexcerpt\ncontent[:2000]`` as one unchunked row at chunk 0, on
    top of this tap's real chunk 0, and the next hourly tap pass put it back.
    poindexter#1033 gave that truncation a ``chunk_text`` of its own, which
    fixed the payload but not the flapping. One builder means one hash, so
    the second writer to arrive dedups and skips instead of clobbering.
    """
    parts: list[str] = []
    if title:
        parts.append(f"# {title}")
    if excerpt:
        parts.append(excerpt)
    if content:
        parts.append(content)
    return "\n\n".join(parts)


class PostsTap:
    """One Document per published post. source_id = post UUID."""

    name = "posts"
    interval_seconds = 3600

    async def extract(
        self,
        pool: Any,  # asyncpg.Pool
        config: dict[str, Any],
    ) -> AsyncIterator[Document]:
        del config  # no per-install config for this Tap today

        async with pool.acquire() as conn:
            rows = await conn.fetch(
                """
                SELECT id, title, slug, content, excerpt, status, published_at,
                       created_at, updated_at
                  FROM posts
                 WHERE status = 'published'
                 ORDER BY published_at DESC NULLS LAST
                """
            )

        logger.info("PostsTap: %d published posts", len(rows))

        for post in rows:
            text = build_post_text(
                title=post["title"],
                excerpt=post["excerpt"],
                content=post["content"],
            )
            if not text.strip():
                continue

            yield Document(
                source_id=str(post["id"]),
                source_table="posts",
                text=text,
                metadata={
                    "post_id": str(post["id"]),
                    "slug": post["slug"],
                    "title": post["title"],
                    "status": post["status"],
                    "published_at": post["published_at"].isoformat()
                        if post["published_at"]
                        else None,
                    "chars": len(text),
                },
                writer="auto-embed",
            )
