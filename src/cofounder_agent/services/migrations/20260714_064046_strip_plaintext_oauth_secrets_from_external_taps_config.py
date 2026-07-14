"""Migration 20260714_064046_strip_plaintext_oauth_secrets_from_external_taps_config: strip plaintext oauth secrets from external_taps config

ISSUE: Glad-Labs/poindexter#857

Background: ``external_taps.config`` is a plain (unencrypted) jsonb column.
The ``gsc_main`` / ``ga4_main`` Singer-tap rows (Google Search Console / GA4)
store their Google OAuth ``client_secret`` (``oauth_client_secret`` on
ga4_main) and ``refresh_token`` inline in ``config.tap_config`` — readable
by any SQL session, pgAdmin browse, or DB dump — inconsistent with the
``app_settings.is_secret=true`` + async ``SiteConfig.get_secret()`` pattern
used everywhere else. The operator setup runbook
(``docs/integrations/setup-gsc-and-ga4.md``) makes it worse: Step 3 already
has the operator store the same values properly encrypted in
``app_settings``, then Step 4 pastes the identical plaintext into
``external_taps.config`` too — a redundant, unencrypted copy that no runtime
code even reads.

``tap.singer_subprocess`` (``services/integrations/handlers/tap_singer_subprocess.py``)
now supports ``config.secret_fields`` — a ``{tap_config_field:
app_settings_key}`` map resolved via ``site_config.get_secret()``
immediately before the tap subprocess is spawned, never persisted back to
the row. This migration re-points the two live rows at it: for each
plaintext secret field still present in ``tap_config``, salvage its value
into the corresponding ``app_settings`` secret via ``plugins.secrets`` ONLY
if that key doesn't already hold a value (the expected path — per the
runbook's Step 3-before-Step-4 ordering, it already does), then strip the
field from ``tap_config`` and record it in ``secret_fields`` instead.

Idempotent: the per-field ``if field not in tap_config: continue`` guard
means a row with no matching plaintext fields left (already migrated, or
never had one) triggers no UPDATE at all. Rows other than gsc_main/ga4_main
— including a fresh/throwaway DB where neither seed row exists, since
they're operator-created via the setup runbook rather than seeded — are
untouched (the query only ever selects those two names).

APPLY PATH: reaches prod when the worker next runs the runner at boot
(``StartupManager._run_migrations``). CI's ``migrations-smoke`` job
validates against a throwaway DB where the SELECT returns zero rows (a
no-op, not a failure) — so a green CI run proves the migration is
well-formed, not that it has run against prod's actual gsc_main/ga4_main
rows.
"""

from __future__ import annotations

import json
import logging
from typing import Any

from plugins.secrets import get_secret, set_secret

logger = logging.getLogger(__name__)


# tap name -> {tap_config field still holding a plaintext secret: the
# app_settings secret key it should resolve from instead}. GA4's tap
# expects `oauth_client_secret` where GSC's expects `client_secret` — the
# two taps package the Google OAuth client secret under different field
# names, confirmed against the live rows (poindexter#857 investigation).
_SECRET_FIELD_MAP: dict[str, dict[str, str]] = {
    "gsc_main": {
        "client_secret": "google_oauth_client_secret",
        "refresh_token": "google_oauth_refresh_token",
    },
    "ga4_main": {
        "oauth_client_secret": "google_oauth_client_secret",
        "refresh_token": "google_oauth_refresh_token",
    },
}


def _parse_config(raw: Any) -> dict[str, Any]:
    """asyncpg returns jsonb as a raw string unless a codec is registered
    on the pool — accept either form, mirroring the same guard in
    tap_singer_subprocess.py."""
    if isinstance(raw, str):
        return json.loads(raw) if raw else {}
    return dict(raw or {})


async def up(pool) -> None:
    """Strip plaintext OAuth secrets from gsc_main/ga4_main's tap_config,
    salvaging into app_settings first if not already stored there.
    """
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            "SELECT id, name, config FROM external_taps WHERE name = ANY($1::text[])",
            list(_SECRET_FIELD_MAP.keys()),
        )
        for row in rows:
            field_map = _SECRET_FIELD_MAP.get(row["name"])
            if not field_map:
                continue

            config = _parse_config(row["config"])
            tap_config = dict(config.get("tap_config") or {})
            secret_fields = dict(config.get("secret_fields") or {})

            stripped_fields: list[str] = []
            for field, settings_key in field_map.items():
                if field not in tap_config:
                    continue
                plaintext_value = tap_config.pop(field)
                stripped_fields.append(field)
                existing = await get_secret(conn, settings_key)
                if not existing and plaintext_value:
                    await set_secret(
                        conn, settings_key, plaintext_value,
                        description=(
                            f"Salvaged from external_taps.{row['name']}"
                            f".config.tap_config.{field} (poindexter#857)"
                        ),
                    )
                secret_fields[field] = settings_key

            if not stripped_fields:
                continue

            config["tap_config"] = tap_config
            config["secret_fields"] = secret_fields
            await conn.execute(
                "UPDATE external_taps SET config = $2::jsonb WHERE id = $1",
                row["id"], json.dumps(config),
            )
            logger.info(
                "Migration strip_plaintext_oauth_secrets_from_external_taps_config: "
                "stripped %s from %s.tap_config",
                sorted(stripped_fields), row["name"],
            )


async def down(pool) -> None:
    """One-way migration — intentionally not reverted.

    The whole point is that the plaintext secret values stop existing in
    ``external_taps.config``; re-inlining them from the (still-encrypted)
    ``app_settings`` copy would recreate the exact exposure this migration
    exists to close.
    """
    return
