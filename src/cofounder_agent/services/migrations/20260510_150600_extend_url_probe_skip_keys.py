"""Add outbound-only URLs to ``operator_url_probe_skip_keys``.

The brain daemon's operator-url probe was emitting 3 false-positive
"surface unreachable" alerts every cycle for these app_settings keys:

  - storage_public_url       — Cloudflare R2 public bucket URL
                               (expects path-prefixed GETs, not /)
  - google_sitemap_ping_url  — Google sitemap submission endpoint
                               (POST-only with sitemap URL param)
  - indexnow_ping_url        — IndexNow search-engine ping endpoint
                               (POST-only with payload)

These three are outbound-only — Poindexter sends to them, never
expects them to answer a generic GET / health probe. The skip list
already contained other outbound URLs (``social_x_url``, etc.) and
``r2_public_url`` (the legacy name for ``storage_public_url``);
this migration just brings the list up to date with the keys that
have been flagging Matt overnight.

Idempotent — overwrites the value with a known-good superset that
also keeps the existing entries.
"""

from __future__ import annotations

import logging

logger = logging.getLogger(__name__)


# Existing entries + the 3 newly-flagged ones. Sorted alphabetically
# for diff-friendliness when this list grows again.
_SKIP_KEYS = ",".join(sorted({
    # Existing
    "social_x_url",
    "social_linkedin_url",
    "oauth_issuer_url",
    "public_site_revalidate_url",
    "gitea_url",
    "r2_public_url",
    "sdxl_server_url",
    "voice_agent_ollama_url",
    "preview_base_url",
    # New — outbound-only URLs that were generating false alerts
    "storage_public_url",
    "google_sitemap_ping_url",
    "indexnow_ping_url",
}))


async def run_migration(conn) -> None:
    await conn.execute(
        "UPDATE app_settings SET value = $1 "
        "WHERE key = 'operator_url_probe_skip_keys'",
        _SKIP_KEYS,
    )
    logger.info(
        "20260510_150600: operator_url_probe_skip_keys extended with "
        "storage_public_url + google_sitemap_ping_url + indexnow_ping_url "
        "(outbound-only URLs that were generating false positives)"
    )
