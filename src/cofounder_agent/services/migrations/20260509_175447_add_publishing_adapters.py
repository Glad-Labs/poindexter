"""Migration 20260509_175447: add publishing_adapters declarative table.

ISSUE: Glad-Labs/poindexter#112

Background — ``services.social_poster._distribute_to_adapters`` carried
hardcoded ``if "bluesky" in enabled`` / ``if "mastodon" in enabled``
branches. Adding a new social platform meant editing the dispatcher,
which is the same anti-pattern the declarative-data-plane siblings
(``external_taps`` #103, ``retention_policies`` #110,
``webhook_endpoints`` #111) were built to retire.

This migration adds ``publishing_adapters`` modeled column-for-column
on those siblings: handler_name + enabled + config + counter columns +
touch trigger. After this lands, registering a new platform is a row
insert plus a ``register_handler("publishing", ...)`` decorator — no
edit to ``social_poster``.

Two seed rows mirror the live adapters: ``bluesky_main`` (enabled=true,
matches the default ``social_distribution_platforms='bluesky'`` seed)
and ``mastodon_main`` (enabled=false, because the
``mastodon_instance_url`` and ``mastodon_access_token`` rows ship empty
out of the box — operators flip enable=true after running
``poindexter publishers set-secret`` for both).
"""

from __future__ import annotations

import logging

logger = logging.getLogger(__name__)


_TOUCH_FN_SQL = """
CREATE OR REPLACE FUNCTION public.publishing_adapters_touch_updated_at()
RETURNS trigger
LANGUAGE plpgsql
AS $$ BEGIN NEW.updated_at = now(); RETURN NEW; END; $$
"""


_TABLE_SQL = """
CREATE TABLE IF NOT EXISTS public.publishing_adapters (
    id uuid DEFAULT gen_random_uuid() NOT NULL,
    name text NOT NULL,
    platform text NOT NULL,
    handler_name text NOT NULL,
    credentials_ref text,
    default_tags jsonb,
    rate_limit_per_day integer,
    enabled boolean DEFAULT false NOT NULL,
    config jsonb DEFAULT '{}'::jsonb NOT NULL,
    metadata jsonb DEFAULT '{}'::jsonb NOT NULL,
    last_run_at timestamp with time zone,
    last_run_status text,
    last_run_duration_ms integer,
    last_error text,
    total_runs bigint DEFAULT 0 NOT NULL,
    total_failures bigint DEFAULT 0 NOT NULL,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    updated_at timestamp with time zone DEFAULT now() NOT NULL,
    CONSTRAINT publishing_adapters_pkey PRIMARY KEY (id),
    CONSTRAINT publishing_adapters_name_key UNIQUE (name)
)
"""


_INDEXES_SQL = (
    "CREATE INDEX IF NOT EXISTS idx_publishing_adapters_enabled "
    "ON public.publishing_adapters USING btree (enabled)",
    "CREATE INDEX IF NOT EXISTS idx_publishing_adapters_platform "
    "ON public.publishing_adapters USING btree (platform)",
    "CREATE INDEX IF NOT EXISTS idx_publishing_adapters_name "
    "ON public.publishing_adapters USING btree (name)",
)


_TRIGGER_SQL = """
DROP TRIGGER IF EXISTS publishing_adapters_touch_updated_at_trg
    ON public.publishing_adapters;
CREATE TRIGGER publishing_adapters_touch_updated_at_trg
    BEFORE UPDATE ON public.publishing_adapters
    FOR EACH ROW
    EXECUTE FUNCTION public.publishing_adapters_touch_updated_at()
"""


_SEEDS_SQL = (
    # Bluesky is the default-enabled platform — matches the
    # social_distribution_platforms='bluesky' seed in 0000_baseline.seeds.sql.
    """
    INSERT INTO publishing_adapters
        (name, platform, handler_name, credentials_ref, enabled, config, metadata)
    VALUES (
        'bluesky_main',
        'bluesky',
        'bluesky',
        'bluesky_',
        TRUE,
        '{}'::jsonb,
        jsonb_build_object('seeded_by', 'poindexter#112')
    )
    ON CONFLICT (name) DO NOTHING
    """,
    # Mastodon ships disabled — instance_url + access_token seed rows are
    # empty strings, so flipping enabled=TRUE without configuring them
    # would just produce skipped-with-error rows. Operator flow:
    #   poindexter publishers set-secret mastodon_main mastodon_access_token <tok>
    #   poindexter settings set mastodon_instance_url https://mastodon.social
    #   poindexter publishers enable mastodon_main
    """
    INSERT INTO publishing_adapters
        (name, platform, handler_name, credentials_ref, enabled, config, metadata)
    VALUES (
        'mastodon_main',
        'mastodon',
        'mastodon',
        'mastodon_',
        FALSE,
        '{}'::jsonb,
        jsonb_build_object('seeded_by', 'poindexter#112')
    )
    ON CONFLICT (name) DO NOTHING
    """,
)


async def up(pool) -> None:
    """Apply the migration.

    Idempotent — every statement uses ``IF NOT EXISTS`` /
    ``CREATE OR REPLACE`` / ``ON CONFLICT DO NOTHING`` so re-running on
    a DB that already has the table is a no-op.
    """
    async with pool.acquire() as conn:
        await conn.execute(_TOUCH_FN_SQL)
        await conn.execute(_TABLE_SQL)
        for stmt in _INDEXES_SQL:
            await conn.execute(stmt)
        await conn.execute(_TRIGGER_SQL)
        for stmt in _SEEDS_SQL:
            await conn.execute(stmt)
        logger.info(
            "Migration 20260509_175447_add_publishing_adapters: applied "
            "(table + 2 seed rows: bluesky_main enabled, mastodon_main disabled)"
        )


async def down(pool) -> None:
    """Drop the table and the touch function.

    Triggers and indexes are removed implicitly when the table drops.
    """
    async with pool.acquire() as conn:
        await conn.execute("DROP TABLE IF EXISTS public.publishing_adapters")
        await conn.execute(
            "DROP FUNCTION IF EXISTS public.publishing_adapters_touch_updated_at()"
        )
        logger.info(
            "Migration 20260509_175447_add_publishing_adapters down: reverted"
        )
