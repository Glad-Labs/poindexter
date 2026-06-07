"""Migration: create skill_catalog table (poindexter#529).

Tracks every SKILL.md that has been installed via ``poindexter skills import``.
One row per skill name (unique); pack groups skills by origin (e.g. "content",
"ops", "imported").  The ``import_hash`` column is the SHA-256 of the raw
SKILL.md bytes — used by the importer to detect unchanged re-installs and by
the ``list`` command to surface staleness.

Also seeds the ``skill_importer_allowed_licenses`` app_setting with the default
SPDX allow-list.  Operators can extend it via:

    poindexter settings set skill_importer_allowed_licenses MIT,Apache-2.0,GPL-3.0

Rollback: drops the table and removes the seeded app_setting.
"""

from __future__ import annotations

import logging

logger = logging.getLogger(__name__)

_CREATE_TABLE = """
CREATE TABLE IF NOT EXISTS skill_catalog (
    id              UUID        PRIMARY KEY DEFAULT gen_random_uuid(),
    name            TEXT        UNIQUE NOT NULL,
    pack            TEXT        NOT NULL DEFAULT 'imported',
    source_url      TEXT,
    license         TEXT        NOT NULL,
    description     TEXT,
    import_hash     TEXT        NOT NULL,
    prompt_count    INT         NOT NULL DEFAULT 0,
    installed_at    TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at      TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
"""

_CREATE_INDEX = """
CREATE INDEX IF NOT EXISTS idx_skill_catalog_pack ON skill_catalog (pack);
"""

_ALLOWED_LICENSES_KEY = "skill_importer_allowed_licenses"
_ALLOWED_LICENSES_VALUE = "MIT,Apache-2.0,BSD-2-Clause,BSD-3-Clause,ISC"
_ALLOWED_LICENSES_CATEGORY = "skills"
_ALLOWED_LICENSES_DESCRIPTION = (
    "Comma-separated list of SPDX license identifiers that "
    "``poindexter skills import`` accepts.  Add identifiers to permit "
    "additional open-source licenses (e.g. GPL-3.0)."
)


async def up(pool) -> None:
    async with pool.acquire() as conn:
        await conn.execute(_CREATE_TABLE)
        await conn.execute(_CREATE_INDEX)
        await conn.execute(
            """
            INSERT INTO app_settings
                (key, value, category, description, is_secret, is_active)
            VALUES ($1, $2, $3, $4, false, true)
            ON CONFLICT (key) DO NOTHING
            """,
            _ALLOWED_LICENSES_KEY,
            _ALLOWED_LICENSES_VALUE,
            _ALLOWED_LICENSES_CATEGORY,
            _ALLOWED_LICENSES_DESCRIPTION,
        )
    logger.info(
        "Migration create_skill_catalog: created skill_catalog table "
        "and seeded %s",
        _ALLOWED_LICENSES_KEY,
    )


async def down(pool) -> None:
    async with pool.acquire() as conn:
        await conn.execute("DROP TABLE IF EXISTS skill_catalog")
        await conn.execute(
            "DELETE FROM app_settings WHERE key = $1",
            _ALLOWED_LICENSES_KEY,
        )
    logger.info(
        "Migration create_skill_catalog down: dropped skill_catalog table "
        "and removed %s",
        _ALLOWED_LICENSES_KEY,
    )
