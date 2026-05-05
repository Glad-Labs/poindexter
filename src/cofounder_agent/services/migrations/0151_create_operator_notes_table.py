"""Migration 0151: create ``operator_notes`` table.

Per Matt's directive (2026-05-04): the dev_diary content is too
dry — feels like a field report, not a dev blog. True personality
needs operator input. This table stores 1-2 sentence "operator
notes" the operator submits during the day, which the dev_diary
bundle includes as ground-truth emotional through-line for the
narrate_bundle atom to weave through the post.

Each row is one note submission. Multiple notes per niche/day are
allowed and get concatenated in chronological order when the
bundle assembles. The operator can write a quick note at any time
("today's regex bug felt cursed, glad it's done"), and the
dev_diary post that evening matches the mood from those notes
while staying grounded in the technical bundle data.

Schema motivation:

- ``niche_slug`` lets the same table serve dev_diary, ai-ml, gaming,
  hardware niches when those grow operator-note flows. Today it's
  dev_diary-only.
- ``note_date`` (DATE not TIMESTAMP) so notes group by the publish
  day they're for, not the wall-clock minute.
- ``mood`` is an optional categorical hint ("slog", "triumph", "flow",
  "frustrated", "curious") — the prompt can use it to pick narrative
  register, but it's never the sole signal; the prose of ``note``
  is the primary content.
- Per `feedback_always_keep_ml_in_mind`: this table is also a
  training-data accumulator. Every operator-edited dev_diary post
  pairs with the operator_notes that fed its bundle — that's a
  voice-calibration corpus for future fine-tuning.

Spec: ``docs/superpowers/specs/2026-05-04-dynamic-pipeline-composition.md``
Memory: ``feedback_dev_diary_voice_is_founder_not_journalist``.
"""

from __future__ import annotations

import logging

logger = logging.getLogger(__name__)


async def _table_exists(conn, table: str) -> bool:
    return bool(
        await conn.fetchval(
            "SELECT EXISTS(SELECT 1 FROM information_schema.tables "
            "WHERE table_name = $1)",
            table,
        )
    )


async def up(pool) -> None:
    async with pool.acquire() as conn:
        if await _table_exists(conn, "operator_notes"):
            logger.info("Migration 0151: operator_notes already exists — skipping")
            return
        await conn.execute(
            """
            CREATE TABLE operator_notes (
              id           BIGSERIAL PRIMARY KEY,
              niche_slug   TEXT NOT NULL,
              note_date    DATE NOT NULL DEFAULT CURRENT_DATE,
              note         TEXT NOT NULL,
              mood         TEXT,
              created_by   TEXT NOT NULL DEFAULT 'operator',
              created_at   TIMESTAMPTZ NOT NULL DEFAULT NOW()
            )
            """
        )
        await conn.execute(
            "CREATE INDEX idx_operator_notes_niche_date "
            "ON operator_notes (niche_slug, note_date DESC)"
        )
        await conn.execute(
            "CREATE INDEX idx_operator_notes_date "
            "ON operator_notes (note_date DESC)"
        )
        logger.info(
            "Migration 0151: created operator_notes table + indexes"
        )


async def down(pool) -> None:
    async with pool.acquire() as conn:
        await conn.execute("DROP TABLE IF EXISTS operator_notes")
        logger.info("Migration 0151 down: dropped operator_notes")
