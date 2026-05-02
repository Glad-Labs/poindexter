"""
Glad Labs Operator MCP Server — private business tools.

This server is the operator layer on top of Poindexter. It is NOT shipped
in the public Poindexter release. It contains tools that only Glad Labs LLC
uses to run its content business: Discord posting, Lemon Squeezy lookups,
customer support routing, prompt-pack subscriber management, etc.

Architecture:
    Poindexter MCP (mcp-server/)         — public, ships with the product
    Glad Labs MCP  (mcp-server-gladlabs/) — private, this file

Both servers share the same local Postgres pool and httpx client. They are
registered as two distinct entries in the Claude Desktop / Claude Code MCP
config so the tool surfaces stay clean and non-overlapping.

Discord bridge: Discord posts go through OpenClaw's Tools Invoke HTTP API
(http://127.0.0.1:18789/tools/invoke) rather than a raw webhook. OpenClaw
already holds the Discord bot token and exposes the message-send action
via its `message` tool, so we get full bot capabilities (read + write +
moderation) instead of a write-only webhook. See
https://docs.openclaw.ai/gateway/tools-invoke-http-api.md and
https://docs.openclaw.ai/channels/discord.md

Pipeline-ops tools (HITL approval, gate config, topic discovery, scheduled
publishing) live in mcp-server/ — they're core Poindexter ops, not operator
business logic.

Usage:
    uv --directory mcp-server-gladlabs run server.py
"""

import logging
import os

import asyncpg
import httpx

from mcp.server.fastmcp import FastMCP

# OAuth helper — local mirror, see oauth_client.py for why
# (Glad-Labs/poindexter#244, mirrors the pattern from #243).
from oauth_client import (  # noqa: E402 — local module
    MCP_GLADLABS_CLIENT_ID_KEY,
    MCP_GLADLABS_CLIENT_SECRET_KEY,
    MCP_GLADLABS_DEFAULT_SCOPES,
    GladlabsMcpOAuthClient,
    oauth_client_from_pool,
)

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("gladlabs-mcp")

LOCAL_DB_DSN = os.getenv(
    "LOCAL_DATABASE_URL",
    "postgresql://poindexter:poindexter-brain-local@localhost:5433/poindexter_brain",
)
POINDEXTER_API_URL = (
    os.getenv("POINDEXTER_API_URL")
    or os.getenv("GLADLABS_API_URL", "http://localhost:8002")
)
POINDEXTER_API_TOKEN = (
    os.getenv("POINDEXTER_API_TOKEN")
    or os.getenv("GLADLABS_API_TOKEN", "")
)

# OpenClaw Gateway — tools/invoke HTTP API. Default Gateway port is 18789.
# See: https://docs.openclaw.ai/gateway/tools-invoke-http-api.md
OPENCLAW_GATEWAY_URL = os.getenv(
    "OPENCLAW_GATEWAY_URL",
    "http://127.0.0.1:18789",
)
OPENCLAW_GATEWAY_TOKEN = os.getenv("OPENCLAW_GATEWAY_TOKEN", "")

# Default Discord channel ID for post/status tools. Falls back to an empty
# string which makes discord_post require an explicit channel_id arg.
GLADLABS_DEFAULT_DISCORD_CHANNEL = os.getenv("GLADLABS_DEFAULT_DISCORD_CHANNEL", "")

# Lazy-initialized shared resources
_pool: asyncpg.Pool | None = None
_http: httpx.AsyncClient | None = None
# Worker-API OAuth client. Built lazily on first call to ``_get_oauth``;
# the gladlabs MCP doesn't currently call the worker API directly (it
# routes Discord through OpenClaw and uses local DB for everything
# else), but the helper is wired now so future tools can call
# ``await (await _get_oauth()).get(...)`` without re-doing the migration.
_oauth: GladlabsMcpOAuthClient | None = None


async def _get_pool() -> asyncpg.Pool:
    global _pool
    if _pool is None:
        _pool = await asyncpg.create_pool(LOCAL_DB_DSN, min_size=1, max_size=3)
    return _pool


async def _get_http() -> httpx.AsyncClient:
    global _http
    if _http is None:
        _http = httpx.AsyncClient(timeout=30.0)
    return _http


async def _get_oauth() -> GladlabsMcpOAuthClient:
    """Get or build the worker-API OAuth client for operator tools.

    Resolution (Glad-Labs/poindexter#244):

    1. ``app_settings.mcp_gladlabs_oauth_client_id`` +
       ``mcp_gladlabs_oauth_client_secret`` → mints + caches a JWT.
    2. ``app_settings.api_token`` (legacy, encrypted) → static Bearer.
    3. ``POINDEXTER_API_TOKEN`` env → static Bearer fallback.

    The first call opens a credential read against the local pool.
    Subsequent calls return the cached client.
    """
    global _oauth
    if _oauth is None:
        pool = await _get_pool()
        _oauth = await oauth_client_from_pool(
            pool,
            base_url=POINDEXTER_API_URL,
            client_id_key=MCP_GLADLABS_CLIENT_ID_KEY,
            client_secret_key=MCP_GLADLABS_CLIENT_SECRET_KEY,
            api_token_key="api_token",
            static_bearer_fallback=POINDEXTER_API_TOKEN or "",
            scopes=None,  # Use the client's full grant.
        )
    return _oauth


async def _openclaw_invoke(tool: str, action: str, args: dict) -> dict:
    """Invoke an OpenClaw tool via the Tools Invoke HTTP API.

    Args:
        tool: The OpenClaw tool name (e.g. "message", "sessions_list").
        action: The action within the tool (e.g. "send", "json").
        args: Action-specific arguments as a dict.

    Returns:
        Parsed JSON response from OpenClaw.

    Raises:
        RuntimeError: If OPENCLAW_GATEWAY_TOKEN is not set or the HTTP call
            returns a non-2xx status.
    """
    if not OPENCLAW_GATEWAY_TOKEN:
        raise RuntimeError(
            "OPENCLAW_GATEWAY_TOKEN is not set. Paste the gateway token into "
            "the gladlabs MCP entry in ~/.claude.json and restart Claude Code."
        )

    client = await _get_http()
    resp = await client.post(
        f"{OPENCLAW_GATEWAY_URL.rstrip('/')}/tools/invoke",
        headers={
            "Authorization": f"Bearer {OPENCLAW_GATEWAY_TOKEN}",
            "Content-Type": "application/json",
        },
        json={"tool": tool, "action": action, "args": args},
    )
    if resp.status_code // 100 != 2:
        raise RuntimeError(
            f"OpenClaw /tools/invoke returned HTTP {resp.status_code}: "
            f"{resp.text[:200]}"
        )
    return resp.json()


mcp = FastMCP("GladLabs", instructions="""
Glad Labs operator MCP server — private business tools that layer on top of
Poindexter. Use these tools to run the Glad Labs content business: post to
Discord, look up customers, manage subscriber lists, etc.

This server is private to Matt / Glad Labs LLC and is NOT part of the public
Poindexter release.
""")


# ============================================================================
# DISCORD TOOLS (via OpenClaw gateway bot)
# ============================================================================

@mcp.tool()
async def discord_post(message: str, channel_id: str = "") -> str:
    """Post a message to the Glad Labs Discord via the OpenClaw bot.

    Routes through OpenClaw's Tools Invoke HTTP API so messages go through
    the existing Discord bot (full bot permissions), not a write-only webhook.
    Requires OPENCLAW_GATEWAY_TOKEN env var to be set on this MCP entry.

    Args:
        message: The message text to post (Discord markdown supported).
        channel_id: Target Discord channel ID. When omitted, uses
            GLADLABS_DEFAULT_DISCORD_CHANNEL if set.

    Returns:
        A human-readable status line describing the result.
    """
    if not OPENCLAW_GATEWAY_TOKEN:
        return (
            "OpenClaw gateway token not configured. Set OPENCLAW_GATEWAY_TOKEN "
            "in the gladlabs MCP env block in ~/.claude.json (and "
            "claude_desktop_config.json) and restart Claude Code."
        )

    target_channel = channel_id or GLADLABS_DEFAULT_DISCORD_CHANNEL
    if not target_channel:
        return (
            "No Discord channel ID provided and GLADLABS_DEFAULT_DISCORD_CHANNEL "
            "is not set. Pass channel_id explicitly or set the default in the "
            "MCP config."
        )

    if not message or not message.strip():
        return "Refusing to post empty message."

    try:
        result = await _openclaw_invoke(
            tool="message",
            action="send",
            args={
                "channel": "discord",
                "target": f"channel:{target_channel}",
                "message": message[:2000],  # Discord per-message cap
            },
        )
        # OpenClaw responses vary — surface the most useful field.
        detail = result.get("id") or result.get("status") or result.get("ok") or ""
        return f"Posted to Discord ({len(message)} chars){f' — {detail}' if detail else ''}."
    except Exception as e:
        return f"Discord post failed: {type(e).__name__}: {e}"


@mcp.tool()
async def discord_status() -> str:
    """Check whether the OpenClaw gateway bridge is configured and reachable.

    Attempts a lightweight ping against the gateway URL. Does NOT post
    anything to any channel.
    """
    lines = ["Glad Labs Discord bridge status:"]
    lines.append(f"  Gateway URL:        {OPENCLAW_GATEWAY_URL}")
    lines.append(f"  Gateway token:      {'set' if OPENCLAW_GATEWAY_TOKEN else 'NOT set'}")
    lines.append(
        f"  Default channel:    {GLADLABS_DEFAULT_DISCORD_CHANNEL or 'NOT set'}"
    )

    if not OPENCLAW_GATEWAY_TOKEN:
        lines.append("")
        lines.append(
            "discord_post will refuse to run until OPENCLAW_GATEWAY_TOKEN is set."
        )
        return "\n".join(lines)

    # Probe the gateway — use the status/base URL, not tools/invoke, so we
    # don't accidentally trigger a tool run. OpenClaw gateway serves a base
    # page at the gateway URL root.
    try:
        client = await _get_http()
        resp = await client.get(OPENCLAW_GATEWAY_URL.rstrip("/") + "/", timeout=5.0)
        if resp.status_code // 100 == 2 or resp.status_code == 404:
            lines.append("")
            lines.append(f"  Gateway reachable:  yes (HTTP {resp.status_code})")
        else:
            lines.append("")
            lines.append(
                f"  Gateway reachable:  no — HTTP {resp.status_code}: "
                f"{resp.text[:100]}"
            )
    except Exception as e:
        lines.append("")
        lines.append(f"  Gateway reachable:  no — {type(e).__name__}: {e}")

    return "\n".join(lines)


# ============================================================================
# OPERATOR HEALTH
# ============================================================================

@mcp.tool()
async def operator_status() -> str:
    """Quick operator-side status: which Glad Labs business tools are configured.

    Useful as a one-shot check that the operator MCP layer is wired correctly.
    """
    lines = ["Glad Labs operator MCP status:"]
    lines.append(f"  Local DB DSN:          {'set' if LOCAL_DB_DSN else 'unset'}")
    lines.append(f"  Poindexter API URL:    {POINDEXTER_API_URL}")
    lines.append(f"  Poindexter API token:  {'set' if POINDEXTER_API_TOKEN else 'unset'}")

    # OAuth credential state (Glad-Labs/poindexter#244). We only check
    # if the helper *would* use OAuth — we don't actually mint, since
    # operator_status is a diagnostic and a 401 here would be
    # misleading noise.
    try:
        oauth = await _get_oauth()
        lines.append(
            f"  OAuth (worker API):     {'OAuth' if oauth.using_oauth else 'legacy static Bearer'}"
        )
    except Exception as e:  # noqa: BLE001
        lines.append(f"  OAuth (worker API):     init failed: {type(e).__name__}: {e}")

    lines.append(f"  OpenClaw gateway URL:  {OPENCLAW_GATEWAY_URL}")
    lines.append(
        f"  OpenClaw gateway token: {'set' if OPENCLAW_GATEWAY_TOKEN else 'NOT set'}"
    )
    lines.append(
        f"  Default Discord channel: {GLADLABS_DEFAULT_DISCORD_CHANNEL or 'NOT set'}"
    )
    lines.append("")
    lines.append("Tools available in this server:")
    lines.append("  - discord_post(message, channel_id) — post via OpenClaw bot")
    lines.append("  - discord_status() — gateway configuration + reachability check")
    lines.append("  - operator_status() — this tool")
    lines.append("")
    lines.append(
        "Pipeline-ops tools (approve/reject, gates_*, topics_*, schedule_*, "
        "publish_at) live in the public Poindexter MCP server (mcp-server/)."
    )
    lines.append("")
    lines.append("Future tools (placeholders, not yet implemented):")
    lines.append("  - lemonsqueezy_recent_orders(days)")
    lines.append("  - lemonsqueezy_lookup_customer(email)")
    lines.append("  - prompt_pack_subscriber_count()")
    lines.append("  - guide_buyer_lookup(email)")
    return "\n".join(lines)


if __name__ == "__main__":
    mcp.run(transport="stdio")
