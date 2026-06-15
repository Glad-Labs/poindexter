"""Migration: stamp app_settings.updated_at on value change.

Forensics gap surfaced by the 2026-06-15 beacon incident (#1594
follow-up): a surgical ``jsonb`` UPDATE to
``operator_url_probe_target_overrides`` changed ``value`` without
touching ``updated_at``, so the row read days-stale and couldn't answer
"when did this setting last change?" during triage.

``app_settings`` already has a ``BEFORE UPDATE OF value`` trigger
(``app_settings_auto_encrypt_trigger``); this adds a sibling that stamps
``updated_at = now()`` on the same event, so every write path — ORM,
settings helper, or ad-hoc SQL — keeps the timestamp honest.

Scoped to ``OF value`` (not all UPDATEs) to mirror the auto-encrypt
trigger and keep ``updated_at`` meaning "when the value last changed",
not "when any column changed". Idempotent: CREATE OR REPLACE the
function and DROP/CREATE the trigger so a re-run can't duplicate it.

Light imports only (migrations-smoke): logging stdlib.
"""

from __future__ import annotations

import logging

logger = logging.getLogger(__name__)

_CREATE_FN = """
CREATE OR REPLACE FUNCTION public.app_settings_set_updated_at() RETURNS trigger
    LANGUAGE plpgsql
    AS $$
BEGIN
    NEW.updated_at = now();
    RETURN NEW;
END;
$$;
"""

_DROP_TRIGGER = (
    "DROP TRIGGER IF EXISTS app_settings_set_updated_at_trigger "
    "ON public.app_settings"
)

_CREATE_TRIGGER = """
CREATE TRIGGER app_settings_set_updated_at_trigger
    BEFORE UPDATE OF value ON public.app_settings
    FOR EACH ROW
    EXECUTE FUNCTION public.app_settings_set_updated_at();
"""


async def up(pool) -> None:
    async with pool.acquire() as conn:
        await conn.execute(_CREATE_FN)
        await conn.execute(_DROP_TRIGGER)
        await conn.execute(_CREATE_TRIGGER)
    logger.info(
        "Migration app_settings_updated_at_trigger: BEFORE UPDATE OF value "
        "trigger installed (stamps updated_at = now())"
    )


async def down(pool) -> None:
    async with pool.acquire() as conn:
        await conn.execute(_DROP_TRIGGER)
        await conn.execute(
            "DROP FUNCTION IF EXISTS public.app_settings_set_updated_at()"
        )
    logger.info("Migration app_settings_updated_at_trigger down: reverted")
