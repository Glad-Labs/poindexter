"""Migration 20260506_220613: seed voice agent brain mode + public join URL.

ISSUE: Half B of the LiveKit + Claude Code voice integration follow-up
to Glad-Labs/poindexter#383 — runtime brain-mode toggle + ``start_voice_call``
MCP tool.

Adds two app_settings the voice surface reads at runtime:

- ``voice_agent_brain_mode`` (default ``ollama``): which LLM stage the
  always-on ``voice-agent-livekit`` container wires in for each new
  pipeline build. Valid values: ``ollama`` (snappy local glm-4.7-5090
  + read-only Poindexter tools) or ``claude-code`` (Max-sub
  ``claude -p`` subprocess bridge with full repo / MCP / edit access).
  This is the canonical "brain mode" setting going forward — the older
  ``voice_agent_brain`` key seeded in 20260505_135518 keeps its
  semantics for backward compatibility (``run_bot`` reads the new key
  first, then falls back to the legacy key).

- ``voice_agent_public_join_url`` (default
  ``https://nightrider.taild4f626.ts.net/voice/join``): the URL the
  ``start_voice_call`` MCP tool returns to the assistant so it can
  hand the operator a tap-to-join link. Hosted on the worker via the
  Tailscale Funnel that already fronts the rest of the public surface.

Both are seeded with NOT NULL defaults (per
``feedback_app_settings_value_not_null`` — ``app_settings.value`` is
NOT NULL, so a NULL seed crashes CI; the empty string would force a
silent fallback later, also forbidden, so we pin a real default).

Idempotent: ``ON CONFLICT (key) DO NOTHING`` — operator-set values are
preserved on re-run, including any pre-existing ``voice_agent_brain``
row from the prior migration.
"""

from __future__ import annotations

import logging

logger = logging.getLogger(__name__)


_SEEDS: list[tuple[str, str, str, str]] = [
    (
        "voice_agent_brain_mode",
        "ollama",
        "voice",
        "LLM stage the always-on voice agent uses on each new pipeline "
        "build. 'ollama' (default) wires the local glm-4.7-5090 + the "
        "three read-only Poindexter tools — snappy, zero incremental "
        "cost. 'claude-code' shells every voice turn out to `claude -p` "
        "under the operator's Max OAuth sub — slower but has full repo / "
        "MCP / edit / bash access for dev-on-the-go. Read by "
        "services/voice_agent_livekit.py:run_bot at pipeline-build time, "
        "so a flip via `set_setting` or the start_voice_call MCP tool "
        "takes effect on the NEXT call without bouncing the container. "
        "Supersedes the legacy ``voice_agent_brain`` key (20260505_135518) "
        "which stays around as a fallback. Valid values: 'ollama', "
        "'claude-code' — anything else is rejected with a structured "
        "error per feedback_no_silent_defaults.",
    ),
    (
        "voice_agent_public_join_url",
        "https://nightrider.taild4f626.ts.net/voice/join",
        "voice",
        "Public URL the operator (or Claude, via the start_voice_call "
        "MCP tool) taps to join the always-on LiveKit voice room. "
        "Defaults to the Tailscale Funnel hostname pointing at the "
        "worker's /voice/join HTML surface (services/routes/voice_routes.py). "
        "Operators on a different deployment (no Tailscale, custom "
        "hostname, port-forwarded local box) override at runtime via "
        "`poindexter set voice_agent_public_join_url <url>`. Read by "
        "the start_voice_call MCP tool — kept DB-backed (not hardcoded) "
        "so the public Poindexter distribution doesn't ship Matt's "
        "tailnet hostname as gospel.",
    ),
]


async def _table_exists(conn, table: str) -> bool:
    return bool(
        await conn.fetchval(
            "SELECT EXISTS(SELECT 1 FROM information_schema.tables "
            "WHERE table_name = $1)",
            table,
        )
    )


async def up(pool) -> None:
    """Apply the migration."""
    async with pool.acquire() as conn:
        if not await _table_exists(conn, "app_settings"):
            logger.info(
                "Table 'app_settings' missing — skipping migration "
                "20260506_220613 (voice_agent_brain_mode seed)"
            )
            return

        inserted = 0
        for key, value, category, description in _SEEDS:
            result = await conn.execute(
                """
                INSERT INTO app_settings
                    (key, value, category, description, is_secret, is_active)
                VALUES ($1, $2, $3, $4, FALSE, TRUE)
                ON CONFLICT (key) DO NOTHING
                """,
                key, value, category, description,
            )
            if result == "INSERT 0 1":
                inserted += 1
        logger.info(
            "Migration 20260506_220613: seeded %d/%d voice agent runtime "
            "settings (remaining were already set by an operator)",
            inserted, len(_SEEDS),
        )


async def down(pool) -> None:
    """Revert the migration."""
    async with pool.acquire() as conn:
        if not await _table_exists(conn, "app_settings"):
            return
        for key, _value, _category, _description in _SEEDS:
            await conn.execute(
                "DELETE FROM app_settings WHERE key = $1",
                key,
            )
        logger.info(
            "Migration 20260506_220613 rolled back: removed %d voice "
            "agent runtime settings",
            len(_SEEDS),
        )
