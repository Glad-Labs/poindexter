"""Migration 20260618_045109: add settings lifecycle metadata columns.

ISSUE: Glad-Labs/poindexter#756

app_settings has ~900 flat keys with no per-key lifecycle metadata:
no owner (which module reads it), no type annotation, no deprecation
marker.  Recurring failure modes:

* Key-rename fallthroughs: a renamed key silently falls back to defaults
  because nothing flags the old key as deprecated (cost-guard spend-limit
  rename, 2026-05-27).
* Orphan rows: removed jobs leave settings rows that linger indefinitely.
* Dangling model keys: *_model settings pointing at uninstalled models.

Adds four nullable columns (except ``deprecated`` which defaults FALSE):

  owner TEXT
    Which module/service is the primary reader.  NULL = unowned /
    cross-cutting concern.  Populated by settings_defaults.METADATA
    for high-risk keys; fills in incrementally as other keys are
    annotated.

  value_type TEXT  CHECK (value_type IN (...))
    Machine-readable type hint for the value string.  Complements the
    get_int / get_bool / get_float SiteConfig accessors. NULL = plain
    string (default).

  deprecated BOOLEAN NOT NULL DEFAULT FALSE
    True when the key has been renamed or superseded.  SiteConfig.get()
    emits a once-per-boot WARNING when a deprecated key is read, so
    half-migrated rename fallthroughs surface in Loki immediately.

  superseded_by TEXT
    The replacement key to migrate to (populated alongside deprecated).
    NULL for keys deprecated without a direct successor.

All four columns are additive — no existing rows change, no lock on
the full table.  The ``IF NOT EXISTS`` guard makes the migration
idempotent on fresh installs that already have the baseline.

Light imports only (migrations-smoke): logging stdlib.
"""

from __future__ import annotations

import logging

logger = logging.getLogger(__name__)

_VALUE_TYPE_CHECK = (
    "'string', 'boolean', 'integer', 'float', "
    "'url', 'model', 'csv', 'json', 'duration'"
)

_ADD_COLUMNS = f"""
ALTER TABLE app_settings
    ADD COLUMN IF NOT EXISTS owner TEXT,
    ADD COLUMN IF NOT EXISTS value_type TEXT
        CHECK (value_type IN ({_VALUE_TYPE_CHECK})),
    ADD COLUMN IF NOT EXISTS deprecated BOOLEAN NOT NULL DEFAULT FALSE,
    ADD COLUMN IF NOT EXISTS superseded_by TEXT;
"""

_DROP_COLUMNS = """
ALTER TABLE app_settings
    DROP COLUMN IF EXISTS owner,
    DROP COLUMN IF EXISTS value_type,
    DROP COLUMN IF EXISTS deprecated,
    DROP COLUMN IF EXISTS superseded_by;
"""


async def up(pool) -> None:
    async with pool.acquire() as conn:
        await conn.execute(_ADD_COLUMNS)
    logger.info(
        "Migration 20260618_045109: added owner/value_type/deprecated/"
        "superseded_by columns to app_settings"
    )


async def down(pool) -> None:
    async with pool.acquire() as conn:
        await conn.execute(_DROP_COLUMNS)
    logger.info("Migration 20260618_045109: reverted — dropped lifecycle columns")
