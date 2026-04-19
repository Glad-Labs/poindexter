"""seed_loader — bootstrap app_settings from the embedded core seed on first boot.

When a fresh Poindexter install comes up, the brain daemon runs this before
anything else. If the `app_settings` table is empty (or the required
boot-critical keys aren't present), we load the core seed from
`brain/seed_app_settings.json` using `INSERT ... ON CONFLICT DO NOTHING` so
any human-applied edits win over the seed.

The core seed is the free-tier starter pack. The paid-tier Quick Start Guide
ships an optimized seed overlay that the CLI applies on top (via
`poindexter premium activate <license-key>` — see Gitea #225).

This module has no external dependencies beyond asyncpg; it runs inside the
brain container which ships asyncpg in its image.
"""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any, List, Dict

import asyncpg

logger = logging.getLogger(__name__)

# Settings the pipeline needs to even boot. If any of these is missing, the
# seed is applied even if the table isn't strictly empty. Add to this list
# only when something genuinely can't start without it.
REQUIRED_KEYS: frozenset[str] = frozenset({
    "site_name",
    "site_url",
    "api_base_url",
    "ollama_base_url",
    "pipeline_writer_model",
    "pipeline_critic_model",
    "pipeline_fallback_model",
    "qa_overall_score_threshold",
    "require_human_approval",
})


def _seed_path() -> Path:
    """Resolve the path to seed_app_settings.json.

    Tried in order:
    1. `/app/seed_app_settings.json` (container layout — brain Dockerfile
       COPIES the seed into /app alongside brain_daemon.py)
    2. Sibling of this file (host layout — `brain/seed_app_settings.json`)
    """
    candidates = [
        Path("/app/seed_app_settings.json"),
        Path(__file__).resolve().parent / "seed_app_settings.json",
    ]
    for p in candidates:
        if p.is_file():
            return p
    raise FileNotFoundError(
        "seed_app_settings.json not found in expected locations: "
        + ", ".join(str(p) for p in candidates)
    )


def load_seed_file() -> List[Dict[str, Any]]:
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


async def _ensure_app_settings_table(conn: asyncpg.Connection) -> None:
    """Create app_settings if it doesn't exist. Matches seed-defaults.sql schema."""
    await conn.execute(
        """
        CREATE TABLE IF NOT EXISTS app_settings (
            id SERIAL PRIMARY KEY,
            key VARCHAR(255) UNIQUE NOT NULL,
            value TEXT DEFAULT '',
            category VARCHAR(100) DEFAULT 'general',
            description TEXT DEFAULT '',
            is_secret BOOLEAN DEFAULT false,
            created_at TIMESTAMPTZ DEFAULT NOW(),
            updated_at TIMESTAMPTZ DEFAULT NOW()
        )
        """
    )


async def seed_app_settings(conn: asyncpg.Connection) -> Dict[str, int]:
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
