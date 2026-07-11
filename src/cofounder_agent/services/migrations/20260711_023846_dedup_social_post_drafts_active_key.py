"""Migration 20260711_023846: dedup social_post_drafts + active-key unique index

ISSUE: Glad-Labs/poindexter#833

The ``social.generate_drafts`` atom runs in canonical_blog's finalize segment
and inserted a fresh ``social_post_drafts`` row per platform on EVERY pass —
so finalize re-runs (preview_gate regen loops, checkpoint restore, task retry)
stacked duplicates. One task accumulated 3 identical Bluesky drafts and all
three were approved: the same promo went out publicly three times. Same
checkpoint-replay bug class as media.persist (#1701) and the external-metrics
tap re-emission (20260704_033500, whose two-step shape this mirrors).

Natural key: ``(pipeline_task_id, platform, COALESCE(platform_config->>'subreddit',''))``
— reddit drafts are legitimately one-per-subreddit; every other platform has
one draft per task.

1. **One-time supersede** — among ACTIVE rows (status ``pending``/``failed``)
   sharing a key, keep the newest by ``created_at`` and mark the older ones
   ``rejected`` (exactly what an operator does to a duplicate today; it also
   removes them from RetryFailedSocialDraftsJob's ``status='failed'`` sweep so
   a duplicate can never be retry-posted). ``posted`` rows are terminal
   history — the pre-fix triple-post stays a true record — and ``rejected``
   rows are already inert; neither is touched.
2. **Partial unique index over active rows** — the arbiter for
   ``SocialDraftsService.create_draft``'s ``ON CONFLICT DO NOTHING`` guarded
   insert (same commit). The predicate covers ``pending`` AND ``failed``
   together so the retry job's failed→pending reset transitions within the
   indexed set and can never collide (at most one active row per key exists).
   ``posted`` is deliberately NOT in the predicate: pre-fix history already
   holds duplicate posted rows, and blocking re-creation after a posted draft
   is the service layer's job (its NOT EXISTS guard spans pending/failed/
   posted).

Both steps are idempotent: fresh installs have an empty table (UPDATE touches
nothing, index builds empty); re-running on a converged install finds no
``rn > 1`` rows and the index already exists.
"""

from __future__ import annotations

import logging

logger = logging.getLogger(__name__)

_INDEX_NAME = "ux_social_post_drafts_active_key"


async def up(pool) -> None:
    async with pool.acquire() as conn:
        async with conn.transaction():
            # 1. Supersede active duplicates, keeping the freshest row per key.
            superseded = await conn.execute(
                """
                UPDATE social_post_drafts
                SET status = 'rejected'
                WHERE id IN (
                    SELECT id FROM (
                        SELECT id,
                               row_number() OVER (
                                   PARTITION BY pipeline_task_id, platform,
                                       COALESCE(platform_config->>'subreddit', '')
                                   ORDER BY created_at DESC, id DESC
                               ) AS rn
                        FROM social_post_drafts
                        WHERE status IN ('pending', 'failed')
                    ) ranked
                    WHERE ranked.rn > 1
                )
                """
            )

            # 2. The arbiter index for create_draft's ON CONFLICT guard.
            await conn.execute(
                f"""
                CREATE UNIQUE INDEX IF NOT EXISTS {_INDEX_NAME}
                ON social_post_drafts (
                    pipeline_task_id, platform,
                    (COALESCE(platform_config->>'subreddit', ''))
                )
                WHERE status IN ('pending', 'failed')
                """
            )

    logger.info(
        "dedup_social_post_drafts_active_key up: %s active duplicates "
        "superseded, unique index %s ensured",
        superseded.split()[-1] if isinstance(superseded, str) else superseded,
        _INDEX_NAME,
    )


async def down(pool) -> None:
    # Drop only the index. The supersede is one-way: resurrecting duplicate
    # active drafts would just re-arm the double-post bug this migration
    # closes, and the superseded rows carried no information the surviving
    # (newest) row lacks.
    async with pool.acquire() as conn:
        await conn.execute(f"DROP INDEX IF EXISTS {_INDEX_NAME}")
    logger.info(
        "dedup_social_post_drafts_active_key down: dropped %s "
        "(supersede itself is one-way, not restored)",
        _INDEX_NAME,
    )
