"""Fix mcp_http_probe discovery path and re-enable (poindexter#670).

The probe was disabled (mcp_http_probe_enabled=false) because
/.well-known/oauth-protected-resource on :8004 returned 404.
That path is the OAuth metadata endpoint built dynamically from the Host
header — not a static liveness route. The real health endpoint is
/healthz, which the MCP HTTP server mounts unconditionally.

Updates:
- mcp_http_probe_discovery_path: '/.well-known/oauth-protected-resource'
  → '/healthz'
- mcp_http_probe_enabled: 'false' → 'true'
"""

from __future__ import annotations

import logging

logger = logging.getLogger(__name__)


async def up(pool) -> None:
    async with pool.acquire() as conn:
        await conn.execute("""
            UPDATE app_settings
               SET value = '/healthz'
             WHERE key = 'mcp_http_probe_discovery_path'
        """)
        await conn.execute("""
            UPDATE app_settings
               SET value = 'true'
             WHERE key = 'mcp_http_probe_enabled'
        """)
    logger.info(
        "mcp_http_probe: discovery_path → /healthz, enabled → true",
    )


async def down(pool) -> None:
    async with pool.acquire() as conn:
        await conn.execute("""
            UPDATE app_settings
               SET value = '/.well-known/oauth-protected-resource'
             WHERE key = 'mcp_http_probe_discovery_path'
        """)
        await conn.execute("""
            UPDATE app_settings
               SET value = 'false'
             WHERE key = 'mcp_http_probe_enabled'
        """)
    logger.info(
        "mcp_http_probe: discovery_path reverted, disabled",
    )
