"""Seed app_settings for the MCP HTTP server liveness probe.

ISSUE: Glad-Labs/poindexter#434

Why: brain/mcp_http_probe.py needs DB-tunable cadence, dedup, target
URL, and an optional auto-recovery launcher path. All idempotent.
"""

from __future__ import annotations

import logging

logger = logging.getLogger(__name__)


_ROWS = [
    (
        "mcp_http_probe_enabled",
        "true",
        "brain",
        "Master switch for brain/mcp_http_probe.py (poindexter#434).",
        False,
    ),
    (
        "mcp_http_probe_interval_minutes",
        "5",
        "brain",
        "Minutes between real probe round-trips. Default 5.",
        False,
    ),
    (
        "mcp_http_probe_timeout_seconds",
        "3",
        "brain",
        "httpx timeout for the localhost probe. Default 3.",
        False,
    ),
    (
        "mcp_http_probe_dedup_hours",
        "1",
        "brain",
        "Minimum hours between repeat alert_events writes while the MCP "
        "server stays unreachable. Default 1h.",
        False,
    ),
    (
        "mcp_http_probe_base_url",
        "http://127.0.0.1:8004",
        "brain",
        "Base URL of the Poindexter MCP HTTP server. Probe appends the "
        "discovery path. Default http://127.0.0.1:8004.",
        False,
    ),
    (
        "mcp_http_probe_discovery_path",
        "/.well-known/oauth-protected-resource",
        "brain",
        "Discovery endpoint path the probe GETs. Returns 200 when the "
        "MCP server is alive.",
        False,
    ),
    (
        "mcp_http_probe_launcher_path",
        "",
        "brain",
        "Absolute path to a launcher script (.cmd on Windows, .sh on "
        "POSIX) that restarts the MCP HTTP server. Empty (default) = "
        "detection only, no auto-recovery. Fire-and-forget; the next "
        "cadence cycle decides whether the restart worked.",
        False,
    ),
    (
        "mcp_http_probe_restart_cap_per_window",
        "3",
        "brain",
        "Max launcher invocations within the rolling restart window. "
        "Prevents busy-loop when the underlying problem is persistent.",
        False,
    ),
    (
        "mcp_http_probe_restart_window_minutes",
        "60",
        "brain",
        "Rolling-window minutes for the restart cap above. Default 60.",
        False,
    ),
]


async def up(pool) -> None:
    async with pool.acquire() as conn:
        for key, value, category, description, is_secret in _ROWS:
            await conn.execute(
                """
                INSERT INTO app_settings
                    (key, value, category, description, is_secret, is_active)
                VALUES ($1, $2, $3, $4, $5, true)
                ON CONFLICT (key) DO NOTHING
                """,
                key, value, category, description, is_secret,
            )
    logger.info(
        "Migration 20260512_220316: mcp_http probe settings seeded "
        "(or already present).",
    )


async def down(pool) -> None:
    async with pool.acquire() as conn:
        await conn.execute(
            """
            DELETE FROM app_settings
             WHERE key LIKE 'mcp_http_probe_%'
            """
        )
    logger.info(
        "Migration 20260512_220316 down: mcp_http probe tunables removed.",
    )
