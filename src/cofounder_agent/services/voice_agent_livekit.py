"""voice_agent_livekit.py — LiveKit-backed voice agent for Poindexter.

The same Whisper → Ollama → Kokoro pipeline as :mod:`services.voice_agent`,
but the local mic is replaced with a LiveKit room participant. That means
multiple humans (and multiple bots) can share a single voice room — the
"conference call with LLMs" surface. First spike for #330 / Codename
ECHO OPERATOR.

## Architecture

::

    Phone / browser ──join──▶  LiveKit room  ◀──join── Poindexter bot
                                    ▲                       │
                                    │                       │
                                    └──── audio mix ────────┘
                                              │
                                              └▶ Whisper STT
                                                   │
                                                   ▼
                                              Ollama LLM (with MCP tools)
                                                   │
                                                   ▼
                                              Kokoro TTS
                                                   │
                                                   └▶ back into the room

The room is the conference; the bot is just a participant. Add a second
bot (different system prompt + tools) by spinning up another instance of
this script with a different ``--role`` — both bots will hear each other
and the human, and turn-taking falls out of VAD + the natural "wait for
silence" behaviour of each pipeline.

## Run (single bot, joining a room called 'matt-test')

    poetry run python -m services.voice_agent_livekit --room matt-test

## Joining the room from a phone or laptop

The fastest path is the LiveKit hosted demo client at
https://meet.livekit.io . Configure:

- LiveKit URL:  ``ws://<your-tailnet>:7880`` (or ``wss://...`` if behind
  Tailscale Funnel)
- Token:        run ``poetry run python -m services.voice_agent_livekit
                --print-client-token --room matt-test --identity phone``

The client token grants the room + a unique identity. Different humans
get different identities so the bot can attribute speech to who.

## Required env vars (one-shot — eventually moves into bootstrap.toml)

    LIVEKIT_URL          ws://localhost:7880
    LIVEKIT_API_KEY      from docker-compose.local.yml
    LIVEKIT_API_SECRET   from docker-compose.local.yml

## Tools

The bot is wired up with a small read-only Poindexter MCP tool set
(check_health, get_post_count, get_budget). Adding more is just
appending to ``_DEFAULT_TOOLS`` below — Pipecat introspects each
function's signature + docstring to build the tool schema for the LLM.
Tool calls execute *inside* the bot process so they don't need an
HTTP roundtrip through the OAuth-gated MCP server.
"""

from __future__ import annotations

import argparse
import asyncio
import logging
import os
import sys
from datetime import timedelta
from pathlib import Path
from typing import Any

from livekit import api
from pipecat.pipeline.runner import PipelineRunner
from pipecat.transports.livekit.transport import LiveKitParams, LiveKitTransport

from services.voice_agent import build_voice_pipeline_task

logging.basicConfig(level=logging.INFO)
log = logging.getLogger("voice_agent_livekit")


# ---------------------------------------------------------------------------
# Tool wrappers — read-only Poindexter ops the bot can call by voice
# ---------------------------------------------------------------------------
#
# These wrap the same async functions ``mcp-server/server.py`` exposes via
# MCP. We import directly rather than going over HTTP because (a) the bot
# already runs in the same process tree as the Postgres pool and (b) the
# OAuth roundtrip would just add latency for no security gain — the bot
# IS the operator's agent.
# ---------------------------------------------------------------------------


# Pipecat's ``register_direct_function`` requires the first parameter
# to be named ``params`` (a ``FunctionCallParams`` Pipecat injects at
# call time). It's stripped from the LLM-facing schema so the model
# only sees the user-facing kwargs (none, in our read-only set).
#
# We DON'T reuse mcp-server/server.py's tool functions even though
# they exist — those use sync urllib with multi-second timeouts that
# would block the audio pipeline's event loop and leave Emma silent
# while she "thinks" for ~30s. Each tool here uses async httpx with
# a strict 4s overall budget. If we don't have an answer fast, the
# user is better off being told something failed than being left to
# wonder if Emma is broken.


_TOOL_TIMEOUT_SECONDS = 4.0


async def _worker_get(path: str) -> dict[str, Any]:
    """GET an endpoint on the worker (no OAuth — it's a local-only call)."""
    import httpx
    api_url = os.environ.get("POINDEXTER_API_URL", "http://localhost:8002")
    async with httpx.AsyncClient(timeout=_TOOL_TIMEOUT_SECONDS) as client:
        resp = await client.get(f"{api_url}{path}")
        resp.raise_for_status()
        return resp.json()


async def check_pipeline_health(params: Any) -> str:
    """Check the current health of the Poindexter content pipeline.

    Returns a one-sentence summary of database, worker, and LLM
    connectivity. Use this when the operator asks how the system is
    doing, whether anything is broken, system status, or health.
    """
    _ = params
    try:
        data = await _worker_get("/api/health")
    except Exception as e:  # noqa: BLE001
        return f"Could not reach the worker. Error: {e}."

    components = data.get("components", {})
    db = components.get("database", "unknown")
    te = components.get("task_executor", {})
    worker_running = te.get("running", False)
    processed = te.get("total_processed", "?")
    in_progress = te.get("in_progress_count", 0)
    pending = te.get("pending_task_count", 0)

    gpu = components.get("gpu", {})
    gpu_busy = gpu.get("busy", False)
    gpu_owner = gpu.get("owner") or "idle"

    parts = [f"system status is {data.get('status', 'unknown')}"]
    parts.append(f"database is {db}")
    if worker_running:
        parts.append(
            f"worker is running, {processed} tasks processed, "
            f"{in_progress} in progress, {pending} pending",
        )
    else:
        parts.append("worker is not running")
    parts.append(f"GPU is {'busy with ' + gpu_owner if gpu_busy else 'idle'}")
    return ". ".join(parts) + "."


async def get_published_post_count(params: Any) -> str:
    """Get the total number of published blog posts on the operator's site.

    Use this when the operator asks how many posts are live, the number
    of articles, or pipeline output volume.
    """
    _ = params
    try:
        data = await _worker_get("/api/posts?limit=1")
    except Exception as e:  # noqa: BLE001
        return f"Could not fetch the post count. Error: {e}."
    total = data.get("total", "unknown number of")
    return f"There are {total} published posts on the site."


async def get_ai_spending_status(params: Any) -> str:
    """Get current AI spending — daily and monthly totals across providers.

    Use this when the operator asks about budget, costs, spend, or how
    much money the system has burned today.
    """
    _ = params
    try:
        data = await _worker_get("/api/costs/summary")
    except Exception as e:  # noqa: BLE001
        # Fall back to a direct DB query if the API doesn't have a
        # summary endpoint — costs live in cost_logs, summed by day/month.
        try:
            return await _spending_from_db()
        except Exception as e2:  # noqa: BLE001
            return f"Could not fetch spending data. {e}; {e2}."
    today = data.get("today_usd", "?")
    month = data.get("month_usd", "?")
    return f"Today AI spending is {today} dollars. Month to date is {month} dollars."


async def _spending_from_db() -> str:
    """Direct cost_logs query as a fallback when the API doesn't ship a summary."""
    import asyncpg
    _ensure_brain_on_path()
    from brain.bootstrap import resolve_database_url
    dsn = resolve_database_url()
    conn = await asyncpg.connect(dsn, timeout=2.0)
    try:
        today = await conn.fetchval(
            "SELECT COALESCE(SUM(cost_usd), 0)::float "
            "FROM cost_logs WHERE created_at >= date_trunc('day', NOW())",
        )
        month = await conn.fetchval(
            "SELECT COALESCE(SUM(cost_usd), 0)::float "
            "FROM cost_logs WHERE created_at >= date_trunc('month', NOW())",
        )
    finally:
        await conn.close()
    return (
        f"Today AI spending is {today:.2f} dollars. "
        f"Month to date is {month:.2f} dollars."
    )


_DEFAULT_TOOLS: list[Any] = [
    check_pipeline_health,
    get_published_post_count,
    get_ai_spending_status,
]


# ---------------------------------------------------------------------------
# LiveKit token helper
# ---------------------------------------------------------------------------


def _mint_token(
    api_key: str,
    api_secret: str,
    *,
    identity: str,
    room: str,
    can_publish: bool = True,
    can_subscribe: bool = True,
    ttl_minutes: int = 360,
) -> str:
    """Mint a LiveKit JWT for the given identity + room."""
    grants = api.VideoGrants(
        room_join=True,
        room=room,
        can_publish=can_publish,
        can_subscribe=can_subscribe,
        can_publish_data=True,
    )
    token = (
        api.AccessToken(api_key=api_key, api_secret=api_secret)
        .with_identity(identity)
        .with_name(identity)
        .with_grants(grants)
        .with_ttl(timedelta(minutes=ttl_minutes))
        .to_jwt()
    )
    return token


def _resolve_livekit_creds(site_config: Any | None = None) -> tuple[str, str, str]:
    """Pull URL + API key + secret.

    Resolution order:
      1. ``site_config.get('voice_agent_livekit_url')`` if a SiteConfig is
         provided (DB-first per the project's standard).
      2. ``LIVEKIT_URL`` env var (used by ``scripts/start-livekit-voice-bot.sh``
         and any local manual invocation).
      3. Hardcoded ``ws://localhost:7880`` dev fallback.

    API key + secret stay in env vars for now — they're the same string
    the LiveKit SFU container reads from compose, so a single place to
    edit (compose env) keeps the two halves in lockstep. When the
    LiveKit creds eventually move into bootstrap.toml or app_settings
    (#383 follow-up), update both ends together.
    """
    url = ""
    if site_config is not None:
        try:
            url = str(site_config.get("voice_agent_livekit_url", "") or "").strip()
        except Exception:  # noqa: BLE001 — site_config absence is OK; fall through
            url = ""
    url = url or os.environ.get("LIVEKIT_URL", "") or "ws://localhost:7880"
    key = os.environ.get("LIVEKIT_API_KEY", "devkey")
    secret = os.environ.get(
        "LIVEKIT_API_SECRET",
        "devsecret_change_me_change_me_change_me",
    )
    return url, key, secret


# ---------------------------------------------------------------------------
# Bootstrap helpers (mirrors the pattern in voice_agent.py)
# ---------------------------------------------------------------------------


def _ensure_brain_on_path() -> None:
    """Add the repo root to sys.path so brain.bootstrap is importable.

    Lookup order:
      1. ``$POINDEXTER_BRAIN_PARENT`` env var (must contain
         ``brain/bootstrap.py``). Used by the docker container, where
         ``brain/`` is mounted under ``/opt/poindexter/`` and the
         walk-up-parents heuristic can't reach it through the read-only
         ``/app`` overlay.
      2. Walk parents of this file until a ``brain/bootstrap.py``
         neighbour appears. Standard local-dev path.
    """
    env_parent = os.environ.get("POINDEXTER_BRAIN_PARENT", "").strip()
    if env_parent:
        candidate = Path(env_parent)
        if (candidate / "brain" / "bootstrap.py").is_file():
            if str(candidate) not in sys.path:
                sys.path.insert(0, str(candidate))
            return
    here = Path(__file__).resolve()
    for parent in here.parents:
        if (parent / "brain" / "bootstrap.py").is_file():
            if str(parent) not in sys.path:
                sys.path.insert(0, str(parent))
            return
    raise RuntimeError(
        "Could not locate brain/bootstrap.py "
        "(set POINDEXTER_BRAIN_PARENT or run from a checkout)"
    )


# ---------------------------------------------------------------------------
# Run loop
# ---------------------------------------------------------------------------


async def run_bot(
    room: str,
    identity: str = "poindexter-bot",
    *,
    brain: str = "ollama",
    project_dir: str | None = None,
) -> None:
    """Join ``room`` as ``identity`` and run the voice pipeline until killed.

    Args:
        room: LiveKit room to join.
        identity: Bot's room identity. Multiple bots in the same room
            need distinct identities.
        brain: Which LLM stage to wire in. ``"ollama"`` (default) uses
            the local glm-4.7-5090 + the three read-only Poindexter tools.
            ``"claude-code"`` swaps in the ClaudeCodeBridge — every voice
            turn shells out to ``claude -p`` under the operator's Max
            sub. Use this for "dev on the go" — Claude has full repo
            access, MCP tools, and edit/bash powers.
        project_dir: When ``brain == "claude-code"``, the directory the
            Claude subprocess runs in. Determines which CLAUDE.md is
            loaded. Defaults to the bot process's cwd.
    """
    _ensure_brain_on_path()
    import asyncpg

    from brain.bootstrap import require_database_url
    from services.site_config import SiteConfig

    # Bootstrap a tiny pool just to read voice_agent_livekit_url before
    # we mint the token. The "real" pool used by build_voice_pipeline_task
    # is created below; keeping the cred lookup self-contained avoids
    # ordering surprises when this is invoked from the always-on
    # container vs. the ad-hoc script.
    _bootstrap_dsn = require_database_url(source="voice_agent_livekit_creds")
    _bootstrap_pool = await asyncpg.create_pool(_bootstrap_dsn, min_size=1, max_size=1)
    try:
        _bootstrap_cfg = SiteConfig()
        await _bootstrap_cfg.load(_bootstrap_pool)
        url, key, secret = _resolve_livekit_creds(_bootstrap_cfg)
    finally:
        await _bootstrap_pool.close()

    token = _mint_token(key, secret, identity=identity, room=room)

    log.info(
        "Joining LiveKit room %r as %r (brain=%s) at %s",
        room, identity, brain, url,
    )

    transport = LiveKitTransport(
        url=url,
        token=token,
        room_name=room,
        params=LiveKitParams(
            audio_in_enabled=True,
            audio_out_enabled=True,
        ),
    )

    @transport.event_handler("on_connected")
    async def _on_connected(_t: Any) -> None:
        log.info("Bot connected to room %r", room)

    @transport.event_handler("on_first_participant_joined")
    async def _on_first(_t: Any, participant_id: str) -> None:
        log.info("First participant joined: %s", participant_id)

    @transport.event_handler("on_participant_disconnected")
    async def _on_part_left(_t: Any, participant_id: str) -> None:
        log.info("Participant left: %s", participant_id)

    dsn = require_database_url(source="voice_agent_livekit")
    pool = await asyncpg.create_pool(dsn, min_size=1, max_size=2)
    try:
        site_config = SiteConfig()
        await site_config.load(pool)

        if brain == "claude-code":
            from services.voice_agent_claude_code import ClaudeCodeBridgeLLMService

            extra = os.environ.get("CLAUDE_BOT_EXTRA_ARGS", "").split()
            sess = os.environ.get("CLAUDE_BOT_SESSION_ID", "").strip() or None
            llm_service = ClaudeCodeBridgeLLMService(
                cwd=project_dir or os.getcwd(),
                extra_args=extra or None,
                session_id=sess,
            )
            if sess:
                log.info("Resuming Claude session_id=%s (preserves prior turns)", sess)
            # The Claude bridge ignores Pipecat-side tools (it has its
            # own MCP harness); we also override the system prompt so
            # we don't double-prompt Claude with Emma's local-LLM
            # persona.
            task = build_voice_pipeline_task(
                transport, site_config, log=log,
                llm=llm_service,
                system_prompt_override=(
                    "You are speaking out loud to Matt over a phone "
                    "call. Keep replies short and natural — under 20 "
                    "seconds of speech unless he asks for more. No "
                    "markdown, no bullet lists, no code blocks; this "
                    "goes through TTS. When you take an action (edit a "
                    "file, run a command, push a PR) summarise the "
                    "outcome in one sentence rather than narrating the "
                    "steps."
                ),
            )
        else:
            task = build_voice_pipeline_task(
                transport, site_config, log=log, tools=_DEFAULT_TOOLS,
            )

        runner = PipelineRunner(handle_sigint=False)
        await runner.run(task)
    finally:
        await pool.close()
        log.info("Voice agent shut down.")


def _print_client_token(room: str, identity: str) -> None:
    _, key, secret = _resolve_livekit_creds()
    tok = _mint_token(
        key, secret, identity=identity, room=room,
        can_publish=True, can_subscribe=True,
    )
    print(tok)


# ---------------------------------------------------------------------------
# Always-on daemon entry point — reads everything from app_settings and runs
# the bot until killed. Used by the ``voice-agent-livekit`` Docker service
# (#383). When the operator disables the surface via
# ``voice_agent_livekit_enabled = false``, the process exits 0 — docker's
# ``unless-stopped`` policy then leaves it stopped without crash-looping.
# ---------------------------------------------------------------------------


async def run_service() -> int:
    """Start the always-on LiveKit voice bot.

    Reads every knob from ``app_settings``:
      - ``voice_agent_livekit_enabled`` — if false/0/no, exit 0 immediately
      - ``voice_agent_room_name``      — LiveKit room to join
      - ``voice_agent_identity``       — bot identity in the room
      - ``voice_agent_brain``          — 'ollama' or 'claude-code'

    Returns the desired process exit code.
    """
    _ensure_brain_on_path()
    import asyncpg

    from brain.bootstrap import require_database_url
    from services.site_config import SiteConfig

    dsn = require_database_url(source="voice_agent_livekit_service")
    pool = await asyncpg.create_pool(dsn, min_size=1, max_size=2)
    try:
        site_config = SiteConfig()
        await site_config.load(pool)

        enabled = str(
            site_config.get("voice_agent_livekit_enabled", "true"),
        ).strip().lower()
        if enabled in {"false", "0", "no", "off"}:
            log.info(
                "voice_agent_livekit_enabled=%s — surface disabled, "
                "exiting 0 so docker leaves us stopped under unless-stopped.",
                enabled,
            )
            return 0

        room = str(
            site_config.get("voice_agent_room_name", "poindexter"),
        ).strip() or "poindexter"
        identity = str(
            site_config.get("voice_agent_identity", "poindexter-bot"),
        ).strip() or "poindexter-bot"
        brain_choice = str(
            site_config.get("voice_agent_brain", "ollama"),
        ).strip().lower() or "ollama"
        if brain_choice not in {"ollama", "claude-code"}:
            # Fail loud — silent fallback to ollama would mask a typo
            # and leave the operator wondering why claude-code never
            # actually engages. (per feedback_no_silent_defaults)
            raise SystemExit(
                f"voice_agent_brain={brain_choice!r} is invalid. "
                f"Valid values: ollama, claude-code.",
            )
    finally:
        # ``run_bot`` opens its own pool (it needs the lifecycle to
        # mirror the Pipecat task), so close this one and avoid double
        # use.
        await pool.close()

    await run_bot(room, identity, brain=brain_choice)
    return 0


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Poindexter voice agent — LiveKit room participant.",
    )
    parser.add_argument(
        "--service", action="store_true",
        help=(
            "Run as the always-on container daemon (#383). Reads room, "
            "identity, brain, and enabled-flag from app_settings; ignores "
            "--room/--identity/--brain. Exits 0 if voice_agent_livekit_"
            "enabled is false."
        ),
    )
    parser.add_argument(
        "--room", default="matt-test",
        help="LiveKit room name to join. Default: matt-test.",
    )
    parser.add_argument(
        "--identity", default="poindexter-bot",
        help="Bot's identity in the room. Useful for distinguishing multiple bots.",
    )
    parser.add_argument(
        "--print-client-token", action="store_true",
        help=(
            "Don't run the bot — just print a JWT a human client can use to "
            "join the same room. Pair with --identity to override the default."
        ),
    )
    parser.add_argument(
        "--brain",
        choices=["ollama", "claude-code"],
        default="ollama",
        help=(
            "LLM stage to wire in. 'ollama' is the snappy local glm-4.7-5090 "
            "with three read-only Poindexter tools. 'claude-code' shells out "
            "to `claude -p` under the operator's Max OAuth sub — slower but "
            "has full repo / MCP / edit access for dev-on-the-go."
        ),
    )
    parser.add_argument(
        "--project-dir",
        default=None,
        help=(
            "Used with --brain=claude-code. The directory `claude` is "
            "spawned in (determines which CLAUDE.md loads). Defaults to "
            "the bot's cwd."
        ),
    )
    args = parser.parse_args()

    if args.print_client_token:
        _print_client_token(args.room, args.identity)
        return

    if args.service:
        try:
            rc = asyncio.run(run_service())
        except KeyboardInterrupt:
            rc = 0
        sys.exit(rc)

    try:
        asyncio.run(
            run_bot(
                args.room, args.identity,
                brain=args.brain, project_dir=args.project_dir,
            ),
        )
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
