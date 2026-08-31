"""Raise the topic_pool retention TTL from 14 to 30 days.

ISSUE: Glad-Labs/poindexter#1036

The taps deposit discovered topics into ``topic_pool`` continuously and
independently of batching, so discovery keeps running while the content
pipeline is paused — 393 topics were ingested during the 47-hour stall of
2026-08-28. What stops during a pause is the *sweep*, which drains the pool
into batches. So a pause makes the pool grow while nothing consumes it.

At a 14-day TTL that backlog starts being deleted: the policy has pruned
7,214 rows over its life, 13 of them during that stall, and the oldest pooled
entry was sitting exactly at the 14-day boundary. The pause was preserving the
topics the operator wanted and the retention policy was quietly discarding
them from underneath.

30 days keeps the original intent — "stale headlines are not worth ranking" is
still true, just on a horizon that survives a fortnight-long review backlog
rather than being defeated by one. It is a value change only; the policy,
filter and age column are untouched.

Idempotent: the UPDATE is a no-op when already 30, and it deliberately matches
on the old value so an operator who has since tuned this themselves is not
overwritten.
"""

from __future__ import annotations

import logging

logger = logging.getLogger(__name__)

_POLICY_ID = "4d1be84b-4dfc-41db-9bfa-4d919011c372"
_OLD_TTL = 14
_NEW_TTL = 30

_NEW_DESC = (
    "Prune pooled topic candidates that no sweep batched within 30 days — "
    "stale headlines are not worth ranking, but the horizon must outlast a "
    "long approval backlog (poindexter#1036)"
)


async def up(pool) -> None:
    """Raise the TTL, only where it is still the seeded default."""
    async with pool.acquire() as conn:
        result = await conn.execute(
            """
            UPDATE retention_policies
               SET ttl_days = $1,
                   metadata = jsonb_set(
                       COALESCE(metadata, '{}'::jsonb), '{description}', to_jsonb($2::text)
                   ),
                   updated_at = NOW()
             WHERE id = $3 AND ttl_days = $4
            """,
            _NEW_TTL, _NEW_DESC, _POLICY_ID, _OLD_TTL,
        )
    logger.info(
        "Migration raise topic_pool TTL 14->30: %s (no-op if already raised "
        "or operator-tuned)", result,
    )


async def down(pool) -> None:
    """Restore the 14-day TTL, only where this migration set 30."""
    async with pool.acquire() as conn:
        await conn.execute(
            "UPDATE retention_policies SET ttl_days = $1, updated_at = NOW() "
            "WHERE id = $2 AND ttl_days = $3",
            _OLD_TTL, _POLICY_ID, _NEW_TTL,
        )
    logger.info("Migration raise topic_pool TTL: reverted to %d", _OLD_TTL)
