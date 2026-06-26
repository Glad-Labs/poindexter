"""Rename sdxl_* app_settings keys to image_gen_*.

The image provider plugin was renamed from 'sdxl' to 'image_gen' as part of
the Z-Image-Turbo rebrand (sdxl is no longer the active model). This migration
updates the three DB-backed settings keys so prod installs converge with the
new names the code now reads.

Keys renamed:
  sdxl_server_url     → image_gen_server_url
  sdxl_enabled        → image_gen_enabled
  enable_sdxl_warmup  → enable_image_gen_warmup

Also updates the operator_url_probe_skip_keys CSV to reference the new
image_gen_server_url name so the URL probe continues to skip-check it.

Fresh installs get the new names from 0000_baseline.seeds.sql; this migration
is a no-op for them (ON CONFLICT DO NOTHING guards + the UPDATE uses WHERE NOT
EXISTS to avoid clobbering already-migrated rows).
"""

from __future__ import annotations

RENAME_PAIRS = [
    ("sdxl_server_url", "image_gen_server_url"),
    ("sdxl_enabled", "image_gen_enabled"),
    ("enable_sdxl_warmup", "enable_image_gen_warmup"),
]


async def up(conn) -> None:
    for old_key, new_key in RENAME_PAIRS:
        # Copy old value to new key only when the new key doesn't exist yet.
        await conn.execute(
            """
            INSERT INTO app_settings (key, value, category, description, is_secret, is_active)
            SELECT $2, value, category, description, is_secret, is_active
            FROM app_settings
            WHERE key = $1
            ON CONFLICT (key) DO NOTHING
            """,
            old_key,
            new_key,
        )
        # Remove the stale old key once the new one is present.
        await conn.execute(
            "DELETE FROM app_settings WHERE key = $1",
            old_key,
        )

    # Fix the sdxl_server_url reference in operator_url_probe_skip_keys.
    await conn.execute(
        """
        UPDATE app_settings
        SET value = replace(value, 'sdxl_server_url', 'image_gen_server_url')
        WHERE key = 'operator_url_probe_skip_keys'
          AND value LIKE '%sdxl_server_url%'
        """,
    )


async def down(conn) -> None:
    for old_key, new_key in RENAME_PAIRS:
        await conn.execute(
            """
            INSERT INTO app_settings (key, value, category, description, is_secret, is_active)
            SELECT $2, value, category, description, is_secret, is_active
            FROM app_settings
            WHERE key = $1
            ON CONFLICT (key) DO NOTHING
            """,
            new_key,
            old_key,
        )
        await conn.execute(
            "DELETE FROM app_settings WHERE key = $1",
            new_key,
        )
    await conn.execute(
        """
        UPDATE app_settings
        SET value = replace(value, 'image_gen_server_url', 'sdxl_server_url')
        WHERE key = 'operator_url_probe_skip_keys'
          AND value LIKE '%image_gen_server_url%'
        """,
    )
