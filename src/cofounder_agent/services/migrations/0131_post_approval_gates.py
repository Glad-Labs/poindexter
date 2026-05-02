"""Migration 0131: per-medium approval gate engine (Glad-Labs/poindexter#24).

Introduces a generic, ordinal-driven HITL approval-gate machinery that
sits ALONGSIDE the legacy single-flag gate system on ``posts``
(``awaiting_gate`` etc., from migration 0100). The legacy machinery is
preserved unchanged so the in-flight final_publish_approval gate keeps
working — the new machinery is additive.

Design (locked with Matt — see issue #24 thread):

- Two independent dimensions:
    * ``--media`` controls what gets GENERATED. Persisted on
      ``posts.media_to_generate`` (TEXT[]). Empty array = none.
    * ``--gates`` controls where the workflow PAUSES for approval.
      Persisted as one row per gate name in ``post_approval_gates``,
      ordered by ``ordinal``. Empty = fully autonomous.

- Two rejection verbs (encoded in the ``state`` column):
    * ``rejected``  → kills the post entirely. ``posts.status='rejected'``.
    * ``revising``  → bounces back to regenerate, with operator notes
      stored in ``metadata.feedback``. State flips back to ``pending``
      after the regen completes.

- Re-opening previously-approved gates is supported via service-layer
  ``--cascade`` flag (invalidates downstream approvals). Without
  cascade, the service raises if any later gate has already been
  approved. No schema change needed — cascade is just bulk-flipping
  later rows back to ``pending``.

Schema
------

``post_approval_gates``:
    id           UUID PRIMARY KEY
    post_id      UUID FK → posts(id) ON DELETE CASCADE
    gate_name    TEXT — one of {topic, draft, podcast, video, short,
                 final, media_generation_failed} (free text — service
                 layer enforces the canonical set)
    ordinal      INT — ordering within this post's gate sequence
    state        TEXT — pending | approved | rejected | revising | skipped
    created_at   TIMESTAMPTZ — when the row was inserted
    decided_at   TIMESTAMPTZ — when state last left 'pending'
    approver     TEXT — operator identity (free text for v1)
    notes        TEXT — rejection reason / revise feedback / approval comment
    metadata     JSONB — per-gate scratchpad (retry counts, feedback for
                 regen stages, deep-link overrides)
    UNIQUE (post_id, gate_name, ordinal)

Plus a partial index on ``(post_id, ordinal) WHERE state='pending'`` for
the hot path query "what's the next thing waiting on a human" — and a
plain ``(post_id)`` index for the per-post detail/show endpoints.

``posts.media_to_generate`` TEXT[] DEFAULT '{}'::text[]:
    Array of medium names to generate after writing
    completes. Examples: ``{}`` (none), ``{podcast}``,
    ``{podcast,video,short}``. Read by publish_service to
    decide which media-generation hooks to fire.

Backfill rules (idempotent):
- Already-published posts: NO gate rows (workflow is over).
- In-flight posts (status='draft' or 'awaiting_approval'): single
  ``final`` gate in 'pending' state at ordinal 0, so the new machinery
  can adopt them without making them disappear from operator views.

Default app_settings seeded:
- ``default_workflow_gates``       = "topic,draft,final"
- ``default_media_to_generate``    = ""
- ``media_generation_retry_limit`` = "2"
- ``approval_gate_topic_enabled``  through ``approval_gate_final_enabled``
   = "true" (one per gate, used for legacy-style toggle UX)

All seeds use ``ON CONFLICT DO NOTHING`` so the operator can pin custom
values before re-running.

Down: drops the new table + column. Backfilled gate rows on in-flight
posts are dropped along with the table. The legacy
``awaiting_gate`` columns on ``posts`` are untouched.
"""

from __future__ import annotations

from services.logger_config import get_logger

logger = get_logger(__name__)


_CREATE_TABLE_SQL = """
CREATE TABLE IF NOT EXISTS post_approval_gates (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    post_id UUID NOT NULL REFERENCES posts(id) ON DELETE CASCADE,
    gate_name TEXT NOT NULL,
    ordinal INT NOT NULL,
    state TEXT NOT NULL DEFAULT 'pending',
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    decided_at TIMESTAMPTZ,
    approver TEXT,
    notes TEXT,
    metadata JSONB NOT NULL DEFAULT '{}'::jsonb,
    UNIQUE (post_id, gate_name, ordinal)
)
"""

_CREATE_INDEX_PENDING = """
CREATE INDEX IF NOT EXISTS idx_post_approval_gates_pending
ON post_approval_gates(post_id, ordinal)
WHERE state = 'pending'
"""

_CREATE_INDEX_POST = """
CREATE INDEX IF NOT EXISTS idx_post_approval_gates_post_id
ON post_approval_gates(post_id)
"""

# Seeds — one row per default app_setting. (key, value, description).
_SEED_SETTINGS: list[tuple[str, str, str]] = [
    (
        "default_workflow_gates",
        "topic,draft,final",
        "Comma-separated gate sequence applied to new posts when "
        "--gates isn't passed. Empty string = fully autonomous "
        "(no human checkpoints). Canonical names: topic, draft, "
        "podcast, video, short, final.",
    ),
    (
        "default_media_to_generate",
        "",
        "Comma-separated list of media to generate alongside each "
        "new post when --media isn't passed. Empty = blog post only. "
        "Canonical names: podcast, video, short.",
    ),
    (
        "media_generation_retry_limit",
        "2",
        "How many times each per-medium generation may fail before "
        "the system escalates to a media_generation_failed gate that "
        "asks the operator what to do.",
    ),
    (
        "approval_gate_topic_enabled",
        "true",
        "Whether the topic gate is enabled by default. Legacy-style "
        "feature flag kept for parity with the existing "
        "approval_gate_<name>_enabled UX (poindexter gates set ...).",
    ),
    (
        "approval_gate_draft_enabled",
        "true",
        "Whether the draft gate is enabled by default.",
    ),
    (
        "approval_gate_podcast_enabled",
        "true",
        "Whether the podcast gate is enabled by default.",
    ),
    (
        "approval_gate_video_enabled",
        "true",
        "Whether the video gate is enabled by default.",
    ),
    (
        "approval_gate_short_enabled",
        "true",
        "Whether the short-video gate is enabled by default.",
    ),
    (
        "approval_gate_final_enabled",
        "true",
        "Whether the final pre-distribution gate is enabled by default.",
    ),
    (
        "approval_gate_media_generation_failed_enabled",
        "true",
        "Whether the auto-escalation gate fires when per-medium "
        "generation hits the retry limit.",
    ),
]


async def up(pool) -> None:
    async with pool.acquire() as conn:
        # 1. New table + indexes
        await conn.execute(_CREATE_TABLE_SQL)
        await conn.execute(_CREATE_INDEX_PENDING)
        await conn.execute(_CREATE_INDEX_POST)

        # 2. Add media_to_generate column on posts (idempotent).
        await conn.execute(
            "ALTER TABLE posts "
            "ADD COLUMN IF NOT EXISTS media_to_generate TEXT[] "
            "NOT NULL DEFAULT '{}'::text[]"
        )

        # 3. Backfill: in-flight posts get a single 'final' gate so the new
        #    machinery can find them. Already-published / archived /
        #    rejected posts are left alone — their workflow is over.
        # ON CONFLICT (post_id, gate_name, ordinal) DO NOTHING keeps this
        # idempotent across re-runs.
        backfill_result = await conn.execute(
            """
            INSERT INTO post_approval_gates
                (post_id, gate_name, ordinal, state)
            SELECT id, 'final', 0, 'pending'
              FROM posts
             WHERE status IN ('draft', 'awaiting_approval')
            ON CONFLICT (post_id, gate_name, ordinal) DO NOTHING
            """
        )
        logger.info(
            "0131: created post_approval_gates + backfilled in-flight posts (%s)",
            backfill_result,
        )

        # 4. Seed default app_settings. Each is ON CONFLICT (key) DO NOTHING
        #    so operator overrides survive re-runs.
        for key, value, description in _SEED_SETTINGS:
            await conn.execute(
                """
                INSERT INTO app_settings (key, value, description, is_active)
                VALUES ($1, $2, $3, TRUE)
                ON CONFLICT (key) DO NOTHING
                """,
                key, value, description,
            )
        logger.info(
            "0131: seeded %d default app_settings keys for the gate engine",
            len(_SEED_SETTINGS),
        )


async def down(pool) -> None:
    async with pool.acquire() as conn:
        # Drop the table first (CASCADE drops dependent rows nothing else
        # FKs into).
        await conn.execute("DROP TABLE IF EXISTS post_approval_gates")
        # Then the column.
        await conn.execute(
            "ALTER TABLE posts DROP COLUMN IF EXISTS media_to_generate"
        )
        # Settings deletes are scoped to the exact key+value pairs we
        # inserted — operator-pinned overrides are preserved.
        for key, value, _description in _SEED_SETTINGS:
            await conn.execute(
                "DELETE FROM app_settings WHERE key = $1 AND value = $2",
                key, value,
            )
        logger.info("0131: rolled back post_approval_gates + media_to_generate")
