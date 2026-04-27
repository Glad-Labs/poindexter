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

Usage:
    uv --directory mcp-server-gladlabs run server.py
"""

import logging
import os

import asyncpg
import httpx

from mcp.server.fastmcp import FastMCP

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
    lines.append("Future tools (placeholders, not yet implemented):")
    lines.append("  - lemonsqueezy_recent_orders(days)")
    lines.append("  - lemonsqueezy_lookup_customer(email)")
    lines.append("  - prompt_pack_subscriber_count()")
    lines.append("  - guide_buyer_lookup(email)")
    return "\n".join(lines)


# ============================================================================
# HITL APPROVAL GATE TOOLS (Glad-Labs/poindexter#145)
#
# Thin wrappers around services.approval_service. The CLI is the canonical
# operator interface; these MCP tools are convenience wrappers so an operator
# in a Claude Code / Claude Desktop session can drive the same flow without
# dropping to a shell.
#
# Each tool resolves the asyncpg pool the rest of this server uses, opens a
# SiteConfig instance loaded from the DB, and calls the service module.
# ============================================================================

import asyncio  # noqa: E402  — late import keeps the original deps block clean
import json     # noqa: E402
import os       # noqa: E402  (already imported above; kept for clarity)
import sys      # noqa: E402


def _ensure_poindexter_on_path() -> None:
    """Add the cofounder_agent source root to ``sys.path`` so we can import
    ``services.approval_service`` from this private MCP server.

    The poindexter package isn't installed into this server's venv (we run
    via ``uv --directory mcp-server-gladlabs run server.py``), so we rely on
    the side-by-side checkout layout: ``mcp-server-gladlabs/server.py`` and
    ``src/cofounder_agent/`` live in the same repo. Resolve relative to this
    file so it works regardless of cwd.
    """
    here = os.path.dirname(os.path.abspath(__file__))
    candidate = os.path.normpath(os.path.join(here, "..", "src", "cofounder_agent"))
    if candidate not in sys.path:
        sys.path.insert(0, candidate)


async def _make_site_config(pool):
    """Construct a SiteConfig the approval-service helpers can consume."""
    _ensure_poindexter_on_path()
    from services.site_config import SiteConfig  # type: ignore[import-not-found]

    cfg = SiteConfig(pool=pool)
    try:
        await cfg.load(pool)
    except Exception as exc:  # pragma: no cover — best-effort load
        logger.warning("Failed to load SiteConfig in MCP wrapper: %s", exc)
    return cfg


async def _approval_pool():
    """Reuse the shared pool from this server."""
    return await _get_pool()


@mcp.tool()
async def approve(
    task_id: str,
    gate_name: str = "",
    feedback: str = "",
) -> str:
    """Approve a content_tasks row at its current (or named) HITL gate.

    Clears the gate and re-queues the pipeline. Same behavior as
    ``poindexter approve <task_id>`` on the CLI.

    Args:
        task_id: UUID of the content_tasks row.
        gate_name: Optional — assert which gate is being approved.
            When empty, the active gate is cleared.
        feedback: Optional operator note recorded in audit_log.

    Returns:
        JSON-encoded result dict, or a human-readable error string.
    """
    _ensure_poindexter_on_path()
    from services.approval_service import (  # type: ignore[import-not-found]
        ApprovalServiceError,
        approve as approve_service,
    )

    try:
        pool = await _approval_pool()
        site_config = await _make_site_config(pool)
        result = await approve_service(
            task_id=task_id,
            gate_name=gate_name or None,
            feedback=feedback or None,
            site_config=site_config,
            pool=pool,
        )
        return json.dumps(result, default=str)
    except ApprovalServiceError as e:
        return f"approve failed: {e}"
    except Exception as e:
        return f"approve unexpected: {type(e).__name__}: {e}"


@mcp.tool()
async def reject(
    task_id: str,
    gate_name: str = "",
    reason: str = "",
) -> str:
    """Reject a content_tasks row at its current (or named) HITL gate.

    Sets the task to the gate's reject status (default: ``rejected``).
    Same behavior as ``poindexter reject <task_id>`` on the CLI.
    """
    _ensure_poindexter_on_path()
    from services.approval_service import (  # type: ignore[import-not-found]
        ApprovalServiceError,
        reject as reject_service,
    )

    try:
        pool = await _approval_pool()
        site_config = await _make_site_config(pool)
        result = await reject_service(
            task_id=task_id,
            gate_name=gate_name or None,
            reason=reason or None,
            site_config=site_config,
            pool=pool,
        )
        return json.dumps(result, default=str)
    except ApprovalServiceError as e:
        return f"reject failed: {e}"
    except Exception as e:
        return f"reject unexpected: {type(e).__name__}: {e}"


@mcp.tool()
async def list_pending(gate_name: str = "", limit: int = 100) -> str:
    """List every task currently paused at any HITL approval gate.

    Args:
        gate_name: Optional filter to a single gate.
        limit: Max rows (default 100).

    Returns:
        JSON-encoded list of pending rows.
    """
    _ensure_poindexter_on_path()
    from services.approval_service import list_pending as list_pending_service  # type: ignore[import-not-found]

    try:
        pool = await _approval_pool()
        rows = await list_pending_service(
            pool=pool, gate_name=gate_name or None, limit=limit,
        )
        return json.dumps(rows, default=str)
    except Exception as e:
        return f"list_pending failed: {type(e).__name__}: {e}"


@mcp.tool()
async def show_pending(task_id: str) -> str:
    """Return the full gate state + artifact for one paused task."""
    _ensure_poindexter_on_path()
    from services.approval_service import (  # type: ignore[import-not-found]
        ApprovalServiceError,
        show_pending as show_pending_service,
    )

    try:
        pool = await _approval_pool()
        row = await show_pending_service(pool=pool, task_id=task_id)
        return json.dumps(row, default=str)
    except ApprovalServiceError as e:
        return f"show_pending failed: {e}"
    except Exception as e:
        return f"show_pending unexpected: {type(e).__name__}: {e}"


@mcp.tool()
async def gates_list() -> str:
    """List every known HITL gate + enabled state + pending count."""
    _ensure_poindexter_on_path()
    from services.approval_service import list_gates  # type: ignore[import-not-found]

    try:
        pool = await _approval_pool()
        site_config = await _make_site_config(pool)
        rows = await list_gates(pool=pool, site_config=site_config)
        return json.dumps(rows, default=str)
    except Exception as e:
        return f"gates_list failed: {type(e).__name__}: {e}"


@mcp.tool()
async def gates_set(gate_name: str, enabled: bool) -> str:
    """Toggle a HITL gate on or off. Writes ``app_settings``.

    Args:
        gate_name: Stable slug, e.g. ``"topic_decision"``.
        enabled: True → ``"on"``, False → ``"off"``.
    """
    _ensure_poindexter_on_path()
    from services.approval_service import set_gate_enabled  # type: ignore[import-not-found]

    try:
        pool = await _approval_pool()
        site_config = await _make_site_config(pool)
        result = await set_gate_enabled(
            gate_name=gate_name,
            enabled=bool(enabled),
            pool=pool,
            site_config=site_config,
        )
        return json.dumps(result, default=str)
    except Exception as e:
        return f"gates_set failed: {type(e).__name__}: {e}"


# ============================================================================
# SCHEDULED PUBLISHING TOOLS (Glad-Labs/poindexter#147)
# ============================================================================
#
# Thin wrappers over services/scheduling_service.py. Mirrors the
# `poindexter schedule ...` CLI commands so an operator chatting with
# Claude can queue up a batch of approved posts the same way.
#


def _result_to_jsonable(result) -> dict:
    """Serialise a ScheduleResult dataclass into a plain dict."""
    return {
        "ok": result.ok,
        "detail": result.detail,
        "count": result.count,
        "rows": result.rows,
    }


@mcp.tool()
async def schedule_batch(
    count: int,
    interval: str,
    start: str,
    quiet_hours: str = "",
    ordered_by: str = "approved_at",
    force: bool = False,
) -> str:
    """Bulk-assign publish slots to the approved-post queue.

    Reads up to ``count`` approved posts in ``ordered_by`` order and
    walks the slot calendar starting from ``start`` stepping by
    ``interval``. Slots inside the quiet-hours window are skipped to
    the next allowed time. Same behaviour as
    ``poindexter schedule batch`` on the CLI.

    Args:
        count: Number of approved posts to schedule.
        interval: Slot spacing — ``30m``, ``1h``, ``1h30m``, ``1d``…
        start: First slot — ISO 8601, ``now``, ``tomorrow 9am``,
            ``next monday 14:00``.
        quiet_hours: ``HH:MM-HH:MM``; empty falls back to the
            ``publish_quiet_hours`` app_setting.
        ordered_by: ``approved_at`` (default) | ``created_at`` |
            ``id`` | ``title``.
        force: Re-schedule posts even if they already have a slot.

    Returns:
        JSON-encoded result envelope.
    """
    _ensure_poindexter_on_path()
    from services.scheduling_service import (  # type: ignore[import-not-found]
        assign_batch as assign_batch_service,
    )

    try:
        pool = await _get_pool()
        site_config = await _make_site_config(pool)
        result = await assign_batch_service(
            count=count,
            interval=interval,
            start=start,
            quiet_hours=quiet_hours or None,
            ordered_by=ordered_by,
            pool=pool,
            site_config=site_config,
            force=force,
        )
        return json.dumps(_result_to_jsonable(result), default=str)
    except Exception as e:
        return f"schedule_batch failed: {type(e).__name__}: {e}"


@mcp.tool()
async def schedule_list(upcoming_only: bool = True) -> str:
    """List every post with a populated publish schedule."""
    _ensure_poindexter_on_path()
    from services.scheduling_service import (  # type: ignore[import-not-found]
        list_scheduled,
    )

    try:
        pool = await _get_pool()
        result = await list_scheduled(
            pool=pool, upcoming_only=upcoming_only,
        )
        return json.dumps(_result_to_jsonable(result), default=str)
    except Exception as e:
        return f"schedule_list failed: {type(e).__name__}: {e}"


@mcp.tool()
async def schedule_show(post_id: str) -> str:
    """Return the schedule detail for a single post."""
    _ensure_poindexter_on_path()
    from services.scheduling_service import (  # type: ignore[import-not-found]
        show_scheduled,
    )

    try:
        pool = await _get_pool()
        result = await show_scheduled(post_id, pool=pool)
        return json.dumps(_result_to_jsonable(result), default=str)
    except Exception as e:
        return f"schedule_show failed: {type(e).__name__}: {e}"


@mcp.tool()
async def schedule_clear(post_id: str = "", clear_all: bool = False) -> str:
    """Drop the schedule on one post or every still-future scheduled post.

    Pass ``post_id`` for a single post, or ``clear_all=True`` for the
    whole upcoming queue. Refuses both at once.
    """
    if (not post_id and not clear_all) or (post_id and clear_all):
        return (
            "schedule_clear: provide either post_id OR clear_all=True, "
            "not both / neither."
        )

    _ensure_poindexter_on_path()
    from services.scheduling_service import (  # type: ignore[import-not-found]
        clear as clear_service,
    )

    try:
        pool = await _get_pool()
        site_config = await _make_site_config(pool)
        ids = None if clear_all else [post_id]
        result = await clear_service(
            post_ids=ids, pool=pool, site_config=site_config,
        )
        return json.dumps(_result_to_jsonable(result), default=str)
    except Exception as e:
        return f"schedule_clear failed: {type(e).__name__}: {e}"


@mcp.tool()
async def publish_at(
    post_id: str,
    when: str = "",
    in_delta: str = "",
    force: bool = False,
) -> str:
    """Schedule a single post at a specific time.

    Provide ``when`` (ISO 8601, ``now``, ``tomorrow 9am``, ``next monday
    14:00``) OR ``in_delta`` (``2h``, ``7d``, ``1h30m``) — exactly one.
    """
    if (not when and not in_delta) or (when and in_delta):
        return (
            "publish_at: provide either when=... OR in_delta=..., "
            "not both / neither."
        )

    _ensure_poindexter_on_path()
    from datetime import datetime, timezone  # type: ignore[import-not-found]

    from services.scheduling_service import (  # type: ignore[import-not-found]
        assign_slot,
        parse_duration,
        parse_when,
    )

    try:
        target = (
            parse_when(when)
            if when
            else datetime.now(timezone.utc) + parse_duration(in_delta)
        )
    except ValueError as e:
        return f"publish_at parse error: {e}"

    try:
        pool = await _get_pool()
        site_config = await _make_site_config(pool)
        result = await assign_slot(
            post_id, target, pool=pool, site_config=site_config, force=force,
        )
        return json.dumps(_result_to_jsonable(result), default=str)
    except Exception as e:
        return f"publish_at failed: {type(e).__name__}: {e}"


# Silence the "imported but unused" warning if asyncio isn't otherwise used.
_ = asyncio


if __name__ == "__main__":
    mcp.run(transport="stdio")
