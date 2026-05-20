"""Add ``mcp_http_probe_base_url`` + ``voice_agent_public_join_url`` to ``operator_url_probe_skip_keys``.

Captured 2026-05-19 stress test (finding #188): the brain's
``operator_url_probe`` was paging 102 times in 12 hours on two
surfaces it shouldn't be probing at all:

  - ``mcp_http_probe_base_url`` — there is a DEDICATED probe
    (``brain/mcp_http_probe.py``) for this surface with its own
    cadence and dedup window. ``operator_url_probe`` picks it up via
    the ``*_url`` suffix matcher and double-pages on every MCP outage.
  - ``voice_agent_public_join_url`` — a POST-only endpoint. HEAD
    returns 405, the GET retry returns 401 (auth-walled). Both look
    "dead" to the probe even though the service is alive. The
    operator already learns about voice outages through the dedicated
    voice-bridge MCP probe.

Same shape as the 2026-05-10 extension (``20260510_150600``) for
outbound-only URLs. Idempotent — overwrites the value with a
known-good superset.
"""

from __future__ import annotations

import logging

logger = logging.getLogger(__name__)


# Existing entries (per 20260510_150600) + the 2 newly-flagged ones.
# Sorted alphabetically for diff-friendliness.
_SKIP_KEYS = ",".join(sorted({
    # From 20260510_150600
    "social_x_url",
    "social_linkedin_url",
    "oauth_issuer_url",
    "public_site_revalidate_url",
    "gitea_url",
    "r2_public_url",
    "sdxl_server_url",
    "voice_agent_ollama_url",
    "preview_base_url",
    "storage_public_url",
    "google_sitemap_ping_url",
    "indexnow_ping_url",
    # New — double-paged surfaces with dedicated probes / non-GET endpoints
    "mcp_http_probe_base_url",
    "voice_agent_public_join_url",
}))


async def up(pool) -> None:
    async with pool.acquire() as conn:
        await conn.execute(
            "UPDATE app_settings SET value = $1, updated_at = NOW() "
            "WHERE key = 'operator_url_probe_skip_keys'",
            _SKIP_KEYS,
        )
        logger.info(
            "20260520_011341: operator_url_probe_skip_keys extended with "
            "mcp_http_probe_base_url + voice_agent_public_join_url "
            "(double-paged surfaces — see finding #188)"
        )


async def down(pool) -> None:
    """One-way migration — no rollback (the prior value is whatever
    the operator's deployment happened to have, and re-introducing the
    spam isn't desirable). No-op.
    """
    logger.info(
        "20260520_011341 down: no-op (one-way superset extension)"
    )
