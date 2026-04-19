"""BrainKnowledgeTap — ingest the brain_knowledge fact table.

Replaces the ``brain_knowledge`` half of Phase 5 in
``scripts/auto-embed.py``. Turns entity/attribute/value facts into
sentences for embedding so agents can search the brain's knowledge
semantically.

Config (``plugin.tap.brain_knowledge``):
- ``limit`` (default ``500``) — recent facts to consider
"""

from __future__ import annotations

import logging
from typing import Any, AsyncIterator

from plugins.tap import Document

logger = logging.getLogger(__name__)


class BrainKnowledgeTap:
    """One Document per brain_knowledge row."""

    name = "brain_knowledge"
    interval_seconds = 3600

    async def extract(
        self,
        pool: Any,  # asyncpg.Pool
        config: dict[str, Any],
    ) -> AsyncIterator[Document]:
        limit = int(config.get("limit", 500))

        async with pool.acquire() as conn:
            rows = await conn.fetch(
                """
                SELECT id, entity, attribute, value, source, confidence, updated_at
                  FROM brain_knowledge
                 ORDER BY updated_at DESC
                 LIMIT $1
                """,
                limit,
            )

        logger.info("BrainKnowledgeTap: %d facts", len(rows))

        for row in rows:
            text = f"{row['entity']}: {row['attribute']} = {row['value']}"
            if row["source"]:
                text += f" (source: {row['source']})"
            # Skip tiny / empty facts — less than 10 chars rarely helps retrieval.
            if not text.strip() or len(text) < 10:
                continue

            yield Document(
                source_id=f"brain_knowledge/{row['id']}",
                source_table="brain",
                text=text,
                metadata={
                    "entity": row["entity"],
                    "attribute": row["attribute"],
                    "source": row["source"],
                    "confidence": float(row["confidence"]) if row["confidence"] else None,
                    "updated_at": row["updated_at"].isoformat() if row["updated_at"] else None,
                },
                writer="brain-daemon",
            )
