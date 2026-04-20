"""BrainDecisionsTap — ingest the brain_decisions audit trail.

Replaces the ``brain_decisions`` half of Phase 5 in
``scripts/auto-embed.py``. Yields one Document per decision row
combining the decision / reasoning / context fields.

Config (``plugin.tap.brain_decisions``):
- ``limit`` (default ``200``) — recent decisions to consider
"""

from __future__ import annotations

import json
import logging
from collections.abc import AsyncIterator
from typing import Any

from plugins.tap import Document

logger = logging.getLogger(__name__)


class BrainDecisionsTap:
    """One Document per brain_decisions row."""

    name = "brain_decisions"
    interval_seconds = 1800

    async def extract(
        self,
        pool: Any,  # asyncpg.Pool
        config: dict[str, Any],
    ) -> AsyncIterator[Document]:
        limit = int(config.get("limit", 200))

        async with pool.acquire() as conn:
            rows = await conn.fetch(
                """
                SELECT id, decision, reasoning, context, confidence, created_at
                  FROM brain_decisions
                 ORDER BY created_at DESC
                 LIMIT $1
                """,
                limit,
            )

        logger.info("BrainDecisionsTap: %d decisions", len(rows))

        for row in rows:
            parts: list[str] = [f"Decision: {row['decision']}"]
            if row["reasoning"]:
                parts.append(f"Reasoning: {row['reasoning']}")
            if row["context"]:
                ctx = row["context"]
                if isinstance(ctx, str):
                    parts.append(f"Context: {ctx[:300]}")
                elif isinstance(ctx, dict):
                    parts.append(f"Context: {json.dumps(ctx)[:300]}")
            text = "\n".join(parts)
            # Skip tiny decisions — nothing useful to retrieve.
            if len(text) < 20:
                continue

            yield Document(
                source_id=f"brain_decisions/{row['id']}",
                source_table="brain",
                text=text,
                metadata={
                    "decision": (row["decision"] or "")[:200],
                    "confidence": float(row["confidence"]) if row["confidence"] else None,
                    "created_at": row["created_at"].isoformat() if row["created_at"] else None,
                },
                writer="brain-daemon",
            )
