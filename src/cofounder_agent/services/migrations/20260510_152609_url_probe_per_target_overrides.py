"""Replace url-probe skip-list extension with per-target probe config.

Per Matt's directive (2026-05-10): skipping a URL hides the problem
instead of fixing it. The earlier migration (``20260510_150600``)
added 3 outbound-only URLs to ``operator_url_probe_skip_keys`` to
silence false-positive alerts; this migration takes the opposite
approach.

What changes:

1. **Revert** ``operator_url_probe_skip_keys`` to its pre-fix value
   (drop the 3 additions). The 3 URLs will be probed again.

2. **Seed** ``operator_url_probe_target_overrides`` — a JSON map of
   ``{app_setting_key: {alive_codes, method, ...}}`` that lets the
   operator explicitly say "for THIS URL, accept 4xx as alive
   because it's a POST-only API". Operator-visible config — no
   hidden allowlist.

3. ``brain/operator_url_probe.py`` reads this setting and applies
   the per-URL behaviour at probe time. Default for unmapped URLs
   stays strict (200–399 = alive).

The 3 URLs that were generating false positives get explicit
``alive_codes: "200-499"`` overrides — covers their normal 4xx
responses while keeping 5xx + network errors as real failures.

Idempotent — UPDATE existing rows, INSERT … ON CONFLICT DO NOTHING
for the new settings row.
"""

from __future__ import annotations

import json
import logging

logger = logging.getLogger(__name__)


# The original (pre-150600) skip-keys value. Keep this list in sync
# with the seed — the rollback path here = the original seed.
_ORIGINAL_SKIP_KEYS = ",".join([
    "social_x_url",
    "social_linkedin_url",
    "oauth_issuer_url",
    "public_site_revalidate_url",
    "gitea_url",
    "r2_public_url",
    "sdxl_server_url",
    "voice_agent_ollama_url",
    "preview_base_url",
])


# Per-URL probe overrides. Each entry maps an app_setting key to a
# dict describing how the probe should treat that URL.
#
# Schema:
#   alive_codes:  "200-499" | "200-399" | comma-separated list
#                 HTTP codes that count as "service alive". Default
#                 is the global 200-399.
#   method:       "HEAD" | "GET" | "OPTIONS"
#                 Probe method. Default HEAD with GET-on-405 retry.
#   reason:       free-text — why this override exists. Operator-
#                 facing documentation; surfaces in the probe summary.
_INITIAL_OVERRIDES = {
    "google_sitemap_ping_url": {
        "alive_codes": "200-499",
        "method": "HEAD",
        "reason": (
            "Google sitemap submission endpoint is POST-only with "
            "'?sitemap=URL' query param. Bare HEAD/GET returns 405 "
            "or 400; that means Google is alive, just refusing the "
            "wrong request shape. 5xx + network errors stay real."
        ),
    },
    "indexnow_ping_url": {
        "alive_codes": "200-499",
        "method": "HEAD",
        "reason": (
            "IndexNow ping is POST-only with a JSON payload. Bare "
            "HEAD/GET returns 4xx; 5xx + network errors stay real."
        ),
    },
    "storage_public_url": {
        "alive_codes": "200-499",
        "method": "HEAD",
        "reason": (
            "Cloudflare R2 public-bucket base URL. Bucket has no / "
            "index — bare HEAD returns 404. We POST/PUT to "
            "path-prefixed URLs under it; the bucket itself is "
            "alive whenever the host answers at all. 5xx + DNS "
            "errors stay real."
        ),
    },
}


_OVERRIDES_DESCRIPTION = (
    "Per-URL probe behavior overrides for the operator-url probe. "
    "JSON map keyed by app_setting key (e.g. 'google_sitemap_ping_url'). "
    "Each value specifies alive_codes (HTTP codes counted as 'service "
    "alive', default '200-399'), method (probe method, default HEAD), "
    "and reason (why the override exists — operator documentation). "
    "Use this to mark outbound-only APIs (sitemap pings, public-bucket "
    "URLs, etc.) where 4xx responses mean 'host alive, request shape "
    "wrong' rather than 'service down'. Without an override the global "
    "default (200-399 = alive) applies. Per Glad-Labs/glad-labs-stack#347 "
    "no-silencing rule: this is the visible alternative to the muted "
    "skip-list approach."
)


async def run_migration(conn) -> None:
    # 1. Revert skip-keys to pre-fix value (drops the 3 outbound URLs
    #    that 150600 muted).
    await conn.execute(
        "UPDATE app_settings SET value = $1 "
        "WHERE key = 'operator_url_probe_skip_keys'",
        _ORIGINAL_SKIP_KEYS,
    )

    # 2. Seed the new override setting. Use INSERT … ON CONFLICT
    #    DO UPDATE so re-running the migration refreshes the value
    #    (in case this list grows in a follow-up).
    await conn.execute(
        """
        INSERT INTO app_settings
            (key, value, category, description, is_secret, is_active)
        VALUES (
            'operator_url_probe_target_overrides',
            $1, 'observability', $2, false, true
        )
        ON CONFLICT (key) DO UPDATE
            SET value = EXCLUDED.value,
                description = EXCLUDED.description
        """,
        json.dumps(_INITIAL_OVERRIDES),
        _OVERRIDES_DESCRIPTION,
    )

    logger.info(
        "20260510_152609: skip-keys reverted to pre-150600 set; "
        "operator_url_probe_target_overrides seeded with 3 entries "
        "(google_sitemap_ping_url, indexnow_ping_url, storage_public_url)"
    )
