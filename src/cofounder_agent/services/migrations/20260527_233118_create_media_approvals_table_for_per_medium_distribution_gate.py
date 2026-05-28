"""Migration 20260527_233118: create media_approvals table.

Adds the per-medium distribution gate for generated audio/video assets.
The blog post itself still publishes on operator approval (no behavior
change there), but the derived media — podcast episodes, long-form
videos, shorts — now sits behind a separate per-medium approval gate
before reaching its distribution surface (podcast RSS feed, YouTube,
YouTube Shorts, etc.).

Why
===
Pre-2026-05-27 the publish path generated and distributed media in
one fire-and-forget step. Matt's quality bar for the AI-generated
podcast + video is still being established, so pushing un-reviewed
audio/video to Apple Podcasts + YouTube is exactly the "no auto
publish" failure the operator playbook calls out
(``feedback_human_approval`` + ``feedback_no_bulk_publish``).

Design
======
- One row per (post_id, medium) — composite PK so re-generation is
  idempotent via ``ON CONFLICT DO NOTHING``.
- ``status`` is the gate: ``pending`` → operator decides → ``approved``
  or ``rejected``. Reading paths (podcast RSS feed query, YouTube
  publish adapter) require ``status='approved'`` before letting the
  media reach its external surface.
- ``decided_by`` captures provenance — ``operator:<user>`` for human
  decisions, ``auto:niche.<slug>`` for the per-niche-per-medium
  auto-approve setting the operator can flip on once trust is earned
  on that niche/medium combination.
- ``notes`` is free-form rationale (matches the existing
  ``pipeline_gate_history`` pattern — Matt-style "rejected because
  the music was off-vibe" notes are useful telemetry).

Mediums
=======
``podcast`` / ``video`` / ``video_short`` — matches the canonical
seam in ``posts.media_to_generate`` populated from
``niches.default_media_to_generate``. A CHECK constraint keeps the
column from drifting to free-text — adding a new medium = ALTER the
constraint as part of the same migration.

Idempotent on prod
==================
``CREATE TABLE IF NOT EXISTS`` + ``CREATE INDEX IF NOT EXISTS`` means
replay on the prod DB no-ops. Fresh CI DB sees the full schema.
"""

from __future__ import annotations

import logging

logger = logging.getLogger(__name__)


async def up(pool) -> None:
    """Apply the migration."""
    async with pool.acquire() as conn:
        await conn.execute(
            """
            CREATE TABLE IF NOT EXISTS public.media_approvals (
                post_id     uuid NOT NULL
                            REFERENCES public.posts(id) ON DELETE CASCADE,
                medium      text NOT NULL
                            CHECK (medium IN ('podcast', 'video', 'video_short')),
                status      text NOT NULL DEFAULT 'pending'
                            CHECK (status IN ('pending', 'approved', 'rejected')),
                decided_at  timestamptz,
                decided_by  text,
                notes       text,
                created_at  timestamptz NOT NULL DEFAULT now(),
                PRIMARY KEY (post_id, medium)
            )
            """,
        )

        # Partial index for the "show me everything pending" operator
        # listing — the only query that doesn't filter by post_id
        # first. ``WHERE status = 'pending'`` keeps the index small
        # since approved/rejected rows are read-rarely.
        await conn.execute(
            """
            CREATE INDEX IF NOT EXISTS ix_media_approvals_pending
            ON public.media_approvals (medium, created_at DESC)
            WHERE status = 'pending'
            """,
        )

        # One-shot grandfather: every currently-published post whose
        # niche policy opted into a medium gets that medium
        # auto-approved. Without this, deployment would empty the
        # podcast RSS feed (and any other distribution surface) until
        # the operator manually approved each existing episode.
        #
        # Proxy used: ``status='published'`` AND ``medium IN
        # media_to_generate``. That's a softer signal than "file
        # exists on disk" (which the DB can't see), but it's the
        # right semantic — if the niche policy said spawn this medium
        # and the post is published, the operator was already happy
        # with the prior fire-and-forget distribution, so grandfather
        # to ``approved``.
        #
        # ``decided_by='migration:grandfather'`` lets the operator
        # filter these out later (e.g. ``WHERE decided_by NOT LIKE
        # 'migration:%'``) if they want to retroactively re-review.
        # Idempotent via ON CONFLICT.
        await conn.execute(
            """
            INSERT INTO media_approvals
                (post_id, medium, status, decided_at, decided_by)
            SELECT
                p.id,
                m.medium,
                'approved',
                now(),
                'migration:grandfather'
            FROM posts p
            CROSS JOIN LATERAL unnest(p.media_to_generate) AS m(medium)
            WHERE p.status = 'published'
              AND m.medium IN ('podcast', 'video', 'video_short')
            ON CONFLICT (post_id, medium) DO NOTHING
            """,
        )

        logger.info(
            "Migration 20260527_233118_create_media_approvals_table_for_per_medium_distribution_gate: applied",
        )


async def down(pool) -> None:
    """Revert: drop the table.

    Reversal loses all approval decisions. On prod that means every
    podcast / video would have to be re-approved by the operator
    after re-running the migration. Flag as a real cost if anyone
    reverts. Dev DBs replay cleanly.
    """
    async with pool.acquire() as conn:
        await conn.execute("DROP TABLE IF EXISTS public.media_approvals")
