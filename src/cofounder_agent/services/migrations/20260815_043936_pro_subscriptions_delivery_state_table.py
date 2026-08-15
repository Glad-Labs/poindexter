"""Migration 20260815_043936: pro_subscriptions — Pro delivery state table

State table for the pay→deliver chain (glad-labs-stack#3216). One row per
Lemon Squeezy subscription, written by ``services/pro_delivery.py`` when
``SyncProSubscriptionsJob`` polls the LS API. The row records the LS
lifecycle status verbatim plus our delivery-side state (which GitHub
account was invited/revoked, and when), so the sync is row-driven: it only
ever touches GitHub users it recorded here, never the operator's own
account or hand-added collaborators.

``github_username`` is filled from LS checkout custom data when the API
exposes it, else by the operator via ``poindexter pro link``. Operator-set
values win — the sync never overwrites a non-NULL username.
"""

from __future__ import annotations

import logging

logger = logging.getLogger(__name__)


async def up(pool) -> None:
    async with pool.acquire() as conn:
        await conn.execute(
            """
            CREATE TABLE IF NOT EXISTS pro_subscriptions (
                subscription_id TEXT PRIMARY KEY,
                order_id TEXT,
                status TEXT NOT NULL,
                product_id TEXT,
                variant_id TEXT,
                customer_email TEXT,
                customer_name TEXT,
                github_username TEXT,
                github_invited_at TIMESTAMPTZ,
                github_revoked_at TIMESTAMPTZ,
                ends_at TIMESTAMPTZ,
                renews_at TIMESTAMPTZ,
                first_seen_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                last_seen_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                raw JSONB
            )
            """
        )
        await conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_pro_subscriptions_status "
            "ON pro_subscriptions(status)"
        )
        logger.info("pro_subscriptions table ready")
