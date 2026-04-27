"""Migration 0100: HITL approval-gate columns on ``posts``.

Companion to migration 0098, which added the same triplet to
``pipeline_tasks`` for mid-pipeline gates. The ``posts`` table sits
*after* the pipeline finishes — rows land here when the writer pipeline
publishes, and the scheduled publisher (services/scheduled_publisher.py)
is what flips ``status='scheduled'`` rows to ``'published'`` once their
``published_at`` slot arrives.

The new ``final_publish_approval`` gate (Matt's ask, 2026-04-27) sits
between scheduling and the actual flip: even after a post has cleared
every mid-pipeline gate and been scheduled into a slot, the operator
gets one more chance to veto right before it goes live.

Schema
------

Three columns mirror migration 0098:

- ``awaiting_gate``  VARCHAR(64) — gate name (e.g. ``"final_publish_approval"``),
  NULL when the row isn't paused.
- ``gate_artifact``  JSONB        — what the operator reviews. For the
  publish gate this is ``{"slug": ..., "title": ..., "preview_url": ...}``.
- ``gate_paused_at`` TIMESTAMPTZ  — when the publisher paused the row.

Plus a partial index ``WHERE awaiting_gate IS NOT NULL`` so the
"posts waiting on a human" query stays cheap.

Idempotent — every DDL uses ``IF NOT EXISTS``.
"""

from services.logger_config import get_logger

logger = get_logger(__name__)


_NEW_COLUMNS = [
    ("awaiting_gate", "VARCHAR(64) NULL"),
    ("gate_artifact", "JSONB NOT NULL DEFAULT '{}'::jsonb"),
    ("gate_paused_at", "TIMESTAMPTZ NULL"),
]


async def up(pool) -> None:
    async with pool.acquire() as conn:
        for column, ddl in _NEW_COLUMNS:
            await conn.execute(
                f"ALTER TABLE posts "
                f"ADD COLUMN IF NOT EXISTS {column} {ddl}"
            )

        await conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_posts_awaiting_gate "
            "ON posts (awaiting_gate, gate_paused_at) "
            "WHERE awaiting_gate IS NOT NULL"
        )

        logger.info(
            "0100: extended posts with %d HITL approval-gate columns",
            len(_NEW_COLUMNS),
        )


async def down(pool) -> None:
    async with pool.acquire() as conn:
        await conn.execute("DROP INDEX IF EXISTS idx_posts_awaiting_gate")
        for column, _ddl in reversed(_NEW_COLUMNS):
            await conn.execute(
                f"ALTER TABLE posts DROP COLUMN IF EXISTS {column}"
            )
        logger.info("0100: rolled back HITL approval-gate columns on posts")
