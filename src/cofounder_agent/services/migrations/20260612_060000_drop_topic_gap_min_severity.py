"""Migration: drop vestigial findings.topic_gap.min_severity app_settings row.

The ``findings.topic_gap.min_severity='info'`` app_settings row was seeded
when the topic_gap delivery policy was first wired (baseline). It was
intended to let ``findings_alert_router._delivery_for`` gate findings at
``info`` severity — but the router's ``_fetch_unrouted_findings`` SQL floor
discards ``info`` findings BEFORE per-kind policy is consulted:

    WHERE severity = ANY('warn','warning','critical')

So the row was never consulted; ``info`` findings for topic_gap would have
been silently dropped regardless of what ``min_severity`` said.

``glad-labs-stack#1471`` corrected the emit site (``analyze_topic_gaps.py``)
to emit at ``severity="warn"``, matching the severity→channel model
(warn→Discord, critical→Telegram, info→log-only). Now that the emit is
correct, the min_severity override is doubly inert: the SQL floor passes
``warn`` through, and ``_delivery_for`` falls back to ``"warning"`` (its
default) when the key is absent — which is the right floor anyway.

Removing the row rather than updating it to ``"warn"`` keeps the policy
clean: per-kind ``min_severity`` overrides exist to RAISE the floor above the
default ``"warning"``, not to lower it below the SQL floor. A value of
``"info"`` was always semantically incorrect here.

Light imports only (migrations-smoke): logging stdlib.
"""

from __future__ import annotations

import logging

logger = logging.getLogger(__name__)

_KEY = "findings.topic_gap.min_severity"


async def up(pool) -> None:
    async with pool.acquire() as conn:
        deleted = await conn.fetchval(
            "DELETE FROM app_settings WHERE key = $1 RETURNING key",
            _KEY,
        )
    if deleted:
        logger.info(
            "Migration drop_topic_gap_min_severity: deleted vestigial row key=%s", _KEY
        )
    else:
        logger.info(
            "Migration drop_topic_gap_min_severity: row key=%s already absent", _KEY
        )


async def down(pool) -> None:
    async with pool.acquire() as conn:
        await conn.execute(
            """
            INSERT INTO app_settings (key, value, category, is_secret, is_active)
            VALUES ($1, 'info', 'general', false, true)
            ON CONFLICT (key) DO NOTHING
            """,
            _KEY,
        )
    logger.info("Migration drop_topic_gap_min_severity down: row restored")
