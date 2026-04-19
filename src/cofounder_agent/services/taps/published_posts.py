"""PostsTap — ingest published posts from the ``posts`` table.

Replaces Phase 2 of ``scripts/auto-embed.py``. Builds embeddable text
from title + excerpt + content, yields one Document per post. Dedup
via ``content_hash`` happens in the runner; this Tap just emits.
"""

from __future__ import annotations

import logging
from typing import Any, AsyncIterator

from plugins.tap import Document

logger = logging.getLogger(__name__)


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
            parts: list[str] = []
            if post["title"]:
                parts.append(f"# {post['title']}")
            if post["excerpt"]:
                parts.append(post["excerpt"])
            if post["content"]:
                parts.append(post["content"])
            text = "\n\n".join(parts)
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
