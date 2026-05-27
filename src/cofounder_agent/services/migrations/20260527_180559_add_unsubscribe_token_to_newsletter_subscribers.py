"""Migration 20260527_180559_add_unsubscribe_token_to_newsletter_subscribers.

Cycle-5 audit finding (#252): ``POST /api/newsletter/unsubscribe`` accepts
``{email, reason}`` with rate-limit-only protection. Anyone who knows or
guesses a subscriber's email can unsubscribe them — the rate limit is
trivially bypassable from distributed sources and isn't an auth check.

Adds a per-subscriber ``unsubscribe_token`` column. The endpoint will
require this token (minted at subscribe time, embedded in the email
template's unsubscribe URL) instead of trusting the inbound email
address. No subscribers exist in production today, so no in-flight
emails to break — confirmed by Matt over Telegram 2026-05-27.

Why TEXT NOT NULL UNIQUE: secrets.token_urlsafe(32) = 43-char base64url
string; UNIQUE so an attacker can't brute-force-collide an existing
token, NOT NULL because the endpoint refuses NULL-token rows. Backfill
is via ``encode(gen_random_bytes(24), 'base64')`` (pgcrypto, already
enabled in baseline schema) for any rows that might exist on dev DBs.
"""

from __future__ import annotations

import logging

logger = logging.getLogger(__name__)


async def up(pool) -> None:
    """Apply the migration.

    Three-step add: column nullable → backfill → NOT NULL + UNIQUE
    index. Splitting backfill from constraint-add lets the constraint
    fail loud if backfill is incomplete (rather than silently leaving a
    row with NULL token + a missing unique index).
    """
    async with pool.acquire() as conn:
        # Step 1: add column (nullable so existing rows don't fail).
        await conn.execute(
            """
            ALTER TABLE newsletter_subscribers
                ADD COLUMN IF NOT EXISTS unsubscribe_token TEXT
            """
        )

        # Step 2: backfill any existing NULL tokens. base64-encoded
        # 24-random-bytes ≈ 32 chars of entropy — same shape as the
        # Python secrets.token_urlsafe(24) the subscribe endpoint will
        # mint going forward. ``gen_random_bytes`` ships with the
        # pgcrypto extension which the baseline already enables.
        await conn.execute(
            """
            UPDATE newsletter_subscribers
            SET unsubscribe_token = replace(replace(
                    encode(gen_random_bytes(24), 'base64'), '+', '-'), '/', '_')
            WHERE unsubscribe_token IS NULL
            """
        )

        # Step 3: tighten the constraint now that no NULLs remain.
        # SET NOT NULL would raise if any row still had NULL — that's
        # exactly the fail-loud behaviour we want per
        # ``feedback_no_silent_defaults``.
        await conn.execute(
            """
            ALTER TABLE newsletter_subscribers
                ALTER COLUMN unsubscribe_token SET NOT NULL
            """
        )

        # Step 4: unique index. Refusing collisions hardens the token
        # against guess-and-replay; the endpoint looks up subscribers
        # by token, so a non-unique row would let two subscribers share
        # one URL.
        await conn.execute(
            """
            CREATE UNIQUE INDEX IF NOT EXISTS
                ix_newsletter_subscribers_unsubscribe_token
                ON newsletter_subscribers (unsubscribe_token)
            """
        )
        logger.info(
            "Migration 20260527_180559_add_unsubscribe_token_to_newsletter_subscribers: applied "
            "(column added, NULLs backfilled, UNIQUE INDEX created)"
        )


async def down(pool) -> None:
    """Revert: drop the unique index then the column. The backfilled
    tokens are lost, but they're regenerable from the next subscribe
    call so this is safely reversible on a dev DB. On prod a rollback
    would force every subscriber to re-subscribe to get a new token —
    flag that as a known cost if anyone reverts.
    """
    async with pool.acquire() as conn:
        await conn.execute(
            "DROP INDEX IF EXISTS ix_newsletter_subscribers_unsubscribe_token"
        )
        await conn.execute(
            "ALTER TABLE newsletter_subscribers DROP COLUMN IF EXISTS unsubscribe_token"
        )
        logger.info(
            "Migration 20260527_180559_add_unsubscribe_token_to_newsletter_subscribers down: "
            "reverted (column + index dropped)"
        )
