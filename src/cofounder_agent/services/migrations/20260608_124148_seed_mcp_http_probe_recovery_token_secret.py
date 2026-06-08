"""Migration 20260608_124148_seed_mcp_http_probe_recovery_token_secret.

Seeds the ``mcp_http_probe_recovery_token`` secret app_settings row.

This is the shared Bearer token used by:
- The brain probe (brain/mcp_http_probe.py) when POSTing to the
  host-side recovery agent at mcp_http_probe_recovery_url.
- The recovery agent (~/.poindexter/scripts/recovery-agent.py) when
  verifying inbound requests.

Seeds an EMPTY value (the sentinel for unset) with is_secret=TRUE so
the auto-encrypt trigger treats it correctly. The operator sets the
actual value after running the migration.

All seeds are INSERT ... ON CONFLICT DO NOTHING — re-runnable, never
clobbers an operator-tuned value.
"""

from __future__ import annotations

import logging

logger = logging.getLogger(__name__)


async def up(pool) -> None:
    """Seed the recovery token secret row (idempotent)."""
    async with pool.acquire() as conn:
        await conn.execute(
            """
            INSERT INTO app_settings (key, value, category, description, is_secret, is_active, updated_at)
            VALUES (
                'mcp_http_probe_recovery_token',
                '',
                'integrations',
                'Bearer token shared between brain probe and host recovery agent (port 9841). '
                'Set to output of: python -c "import secrets; print(secrets.token_hex(32))"',
                TRUE,
                TRUE,
                NOW()
            )
            ON CONFLICT (key) DO NOTHING
            """,
        )
        logger.info(
            "Migration seed_mcp_http_probe_recovery_token_secret: seeded "
            "mcp_http_probe_recovery_token (ON CONFLICT DO NOTHING)",
        )


async def down(pool) -> None:
    """Remove the seeded secret row."""
    async with pool.acquire() as conn:
        await conn.execute(
            "DELETE FROM app_settings WHERE key = 'mcp_http_probe_recovery_token'",
        )
        logger.info(
            "Migration seed_mcp_http_probe_recovery_token_secret down: "
            "removed mcp_http_probe_recovery_token",
        )
