"""seed_loader — bootstrap app_settings from the embedded core seed on every boot.

The brain daemon runs this at startup, before anything else, and the brain
container starts before the worker. It upserts every row of
`brain/seed_app_settings.json`. A missing key is inserted. An empty value is
refilled, since empty means unconfigured. A non-empty value is left alone, so
human edits win over the seed. The refill still fires after `poindexter setup`,
which leaves identity keys like `site_name` and `company_name` empty. Once the
stack comes up they hold this seed's runnable placeholders.

The core seed is the free-tier starter pack. The Pro tier's tuned seed
ships through the private `poindexter-pro` repo (GitHub collaborator
invite, glad-labs-stack#3216) and is applied by that repo's own scripts,
never by this loader. Operator-specific values are applied separately by
`settings_defaults.apply_operator_overrides` from the mirror-stripped
`services/operator_overrides.py`. That overlay reads this seed through
`load_seed_file` and treats a row still holding this seed's value as untuned,
so the operator's values replace the placeholders. See
`project-oss-vs-operator-model-defaults`.

This module has no external dependencies beyond asyncpg; it runs inside the
brain container which ships asyncpg in its image.

It also creates `app_settings` when the table doesn't exist yet. On a fresh
`docker compose up` that is the normal case: the brain boots before the worker
has run a single migration, so the table this module creates is the one every
migration then runs against, and it must be exactly the table
`0000_baseline.schema.sql` declares (`APP_SETTINGS_DDL`). A narrower one
restart-looped the worker on every compose-first install until 2026-09-28
(poindexter#1097).
"""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any

import asyncpg

logger = logging.getLogger(__name__)

# Settings the pipeline needs to even boot. The seed runs in full on every boot
# regardless (Gitea #236); this list only picks the boot log line, which names
# any of these found missing or empty. Add to it only when something genuinely
# can't start without the key, and only a key seed_app_settings.json ships with
# a non-empty value. The seed can't supply any other, so the brain would report
# it missing on every boot: qa_overall_score_threshold did exactly that from
# its retirement (#2281, 2026-07-11) until 2026-09-28, and nothing ever read it.
# Pinned by tests/unit/services/test_brain_seed_loader.py.
REQUIRED_KEYS: frozenset[str] = frozenset({
    "site_name",
    "site_url",
    "api_base_url",
    "ollama_base_url",
    "pipeline_writer_model",
    "pipeline_critic_model",
    "pipeline_fallback_model",
    "require_human_approval",
})


def _seed_path() -> Path:
    """Resolve the path to seed_app_settings.json.

    The seed ships as a sibling of this file -- ``poindexter/brain/seed_app_settings.json``
    on the host and ``/app/poindexter/brain/seed_app_settings.json`` in the container
    (the image COPYs the whole package; poindexter#1046 step 2 retired the flat
    ``/app/seed_app_settings.json`` copy).
    """
    candidates = [
        Path(__file__).resolve().parent / "seed_app_settings.json",
    ]
    for p in candidates:
        if p.is_file():
            return p
    raise FileNotFoundError(
        "seed_app_settings.json not found in expected locations: "
        + ", ".join(str(p) for p in candidates)
    )


def load_seed_file() -> list[dict[str, Any]]:
    """Read and validate the core seed file. Returns the list of setting rows."""
    path = _seed_path()
    with path.open("r", encoding="utf-8") as f:
        data = json.load(f)
    settings = data.get("settings", [])
    if not isinstance(settings, list):
        raise ValueError(f"seed file {path} has no 'settings' list")
    for row in settings:
        if "key" not in row or "value" not in row:
            raise ValueError(f"seed row missing key/value: {row!r}")
    return settings


async def _settings_rows_present(conn: asyncpg.Connection) -> int:
    """Return count of rows in app_settings (0 if table doesn't exist)."""
    try:
        return await conn.fetchval("SELECT COUNT(*) FROM app_settings") or 0
    except asyncpg.exceptions.UndefinedTableError:
        return 0


async def _missing_required_keys(conn: asyncpg.Connection) -> set[str]:
    """Return the required keys that are NOT present (or present but empty)."""
    try:
        rows = await conn.fetch(
            "SELECT key, value FROM app_settings WHERE key = ANY($1)",
            list(REQUIRED_KEYS),
        )
    except asyncpg.exceptions.UndefinedTableError:
        return set(REQUIRED_KEYS)
    present_with_value = {r["key"] for r in rows if r["value"]}
    return set(REQUIRED_KEYS) - present_with_value


# The app_settings table exactly as ``0000_baseline.schema.sql`` declares it.
# On a compose-first install the brain boots before the worker has run a single
# migration, so this CREATE is the one that builds the table and the baseline's
# own ``CREATE TABLE IF NOT EXISTS`` finds it already there. It used to create 8
# of the 14 columns, and the baseline then died on ``idx_app_settings_is_active``
# on every such install (poindexter#1097). Column entries are copied from the
# dump verbatim. ``id`` is ``serial`` because the dump spells the same thing as a
# separate sequence, default and owner. The primary key and unique key are the
# ones the dump adds with ALTER TABLE, under the same names. Pinned to the dump by
# tests/unit/brain/test_seed_loader_app_settings_ddl.py: after a squash that
# changes app_settings, copy the new entries here.
APP_SETTINGS_DDL = """
CREATE TABLE IF NOT EXISTS app_settings (
    id serial NOT NULL,
    key character varying(255) NOT NULL,
    value text DEFAULT ''::text NOT NULL,
    category character varying(100) DEFAULT 'general'::character varying,
    description text DEFAULT ''::text,
    is_secret boolean DEFAULT false,
    created_at timestamp with time zone DEFAULT now(),
    updated_at timestamp with time zone DEFAULT now(),
    is_active boolean DEFAULT true NOT NULL,
    owner text,
    value_type text,
    deprecated boolean DEFAULT false NOT NULL,
    superseded_by text,
    last_read_at timestamp with time zone,
    CONSTRAINT app_settings_value_type_check CHECK ((value_type = ANY (ARRAY['string'::text, 'boolean'::text, 'integer'::text, 'float'::text, 'url'::text, 'model'::text, 'csv'::text, 'json'::text, 'duration'::text]))),
    CONSTRAINT app_settings_pkey PRIMARY KEY (id),
    CONSTRAINT app_settings_key_key UNIQUE (key)
)
"""


async def _ensure_app_settings_table(conn: asyncpg.Connection) -> None:
    """Create app_settings, in the baseline's shape, if it doesn't exist yet.

    A no-op on any install whose migrations already ran. It does not widen a
    table an older brain created: the baseline migration converges that one
    when the worker runs it.
    """
    await conn.execute(APP_SETTINGS_DDL)


async def seed_app_settings(conn: asyncpg.Connection) -> dict[str, int]:
    """Apply the core seed. Idempotent; safe to call on every boot.

    Returns a summary dict: {"inserted": N, "skipped_existing": M, "total_seed": K}.
    """
    await _ensure_app_settings_table(conn)

    rows_before = await _settings_rows_present(conn)
    missing_required = await _missing_required_keys(conn)

    seed_rows = load_seed_file()

    # Always run the INSERT loop — it's idempotent (ON CONFLICT DO UPDATE
    # only fires when the existing value is empty; otherwise DO NOTHING).
    # The previous fast-path skipped new seed keys added to the JSON file
    # after an install had already booted, forcing manual psql INSERTs
    # whenever a new seed key was introduced (Gitea #236). Cost of the
    # loop on a populated DB is ~70 upserts × ~1ms = ~70ms on startup,
    # which is well worth the "new JSON keys land automatically" property.
    if rows_before == 0:
        logger.info(
            f"seed: app_settings is empty; applying full core seed "
            f"({len(seed_rows)} settings)"
        )
    elif missing_required:
        logger.info(
            f"seed: app_settings has {rows_before} rows but is missing "
            f"{len(missing_required)} required keys "
            f"({', '.join(sorted(missing_required))}); applying seed"
        )
    else:
        logger.info(
            f"seed: app_settings has {rows_before} rows; upserting "
            f"{len(seed_rows)} seed keys (only missing/empty values change)"
        )

    # Upsert policy:
    #   - row missing           → INSERT the seed value
    #   - row present, non-empty → keep user's value (DO NOTHING)
    #   - row present, empty    → UPDATE to seed value (empty means unconfigured,
    #                             not an intentional blank — otherwise a boot-critical
    #                             key blanked by accident would never recover)
    inserted = 0
    refilled = 0
    for row in seed_rows:
        result = await conn.execute(
            """
            INSERT INTO app_settings (key, value, category, description)
            VALUES ($1, $2, $3, $4)
            ON CONFLICT (key) DO UPDATE
              SET value = EXCLUDED.value,
                  updated_at = NOW()
              WHERE app_settings.value = '' OR app_settings.value IS NULL
            """,
            row["key"],
            row["value"],
            row.get("category", "general"),
            row.get("description", ""),
        )
        # asyncpg returns "INSERT 0 1" on new row, "INSERT 0 1" on update
        # (the WHERE on the DO UPDATE branch still counts as 1 row written),
        # and "INSERT 0 0" when the WHERE suppressed the update. Track the
        # distinction by re-reading the row after the write.
        if result.endswith(" 1"):
            # Either newly inserted or refilled from empty. Disambiguate.
            was_present = await conn.fetchval(
                "SELECT created_at = updated_at FROM app_settings WHERE key = $1",
                row["key"],
            )
            if was_present:
                inserted += 1
            else:
                refilled += 1

    skipped = len(seed_rows) - inserted - refilled
    logger.info(
        f"seed: applied — {inserted} new rows, {refilled} refilled (were empty), "
        f"{skipped} already populated, {len(seed_rows)} total in seed"
    )
    return {
        "inserted": inserted,
        "refilled": refilled,
        "skipped_existing": skipped,
        "total_seed": len(seed_rows),
    }
