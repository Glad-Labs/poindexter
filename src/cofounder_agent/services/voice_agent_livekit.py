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
from pathlib import Path
from typing import Any

from pipecat.pipeline.runner import PipelineRunner
from pipecat.transports.livekit.transport import LiveKitParams, LiveKitTransport

from services.voice_agent import build_voice_pipeline_task
from services.voice_pipecat import (
    mint_livekit_token as _shared_mint_livekit_token,
)
from services.voice_pipecat import (
    resolve_livekit_creds as _shared_resolve_livekit_creds,
)
from services.voice_pipecat import (
    resolve_livekit_creds_async as _shared_resolve_livekit_creds_async,
)

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
# CRITICAL: tool results must be delivered via ``params.result_callback(...)``,
# NOT via ``return``. Pipecat's runner ignores the function's return value;
# what it does is broadcast a ``FunctionCallInProgressFrame`` (which the
# universal aggregator stamps as the literal "IN_PROGRESS" placeholder in
# the LLM context) and then waits for the function to call its
# ``result_callback``, which broadcasts ``FunctionCallResultFrame`` and
# replaces the placeholder with the real result. A function that ``return``s
# a string instead leaks the "IN_PROGRESS" sentinel into the conversation
# forever, and the LLM keeps re-issuing the same tool call thinking the
# previous one is still running. (Bug discovered 2026-05-05; fix in
# fix/voice-agent-in-progress-tool-results.)
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


async def check_pipeline_health(params: Any) -> None:
    """Check the current health of the Poindexter content pipeline.

    Returns a one-sentence summary of database, worker, and LLM
    connectivity. Use this when the operator asks how the system is
    doing, whether anything is broken, system status, or health.
    """
    result = await _check_pipeline_health_text()
    await params.result_callback(result)


async def _check_pipeline_health_text() -> str:
    """Build the spoken summary string for ``check_pipeline_health``.

    Split out from the tool entry point so the unit tests can assert on
    the actual payload without having to hand-roll a full
    ``FunctionCallParams`` (which requires an LLMService instance).
    """
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


async def get_published_post_count(params: Any) -> None:
    """Get the total number of published blog posts on the operator's site.

    Use this when the operator asks how many posts are live, the number
    of articles, or pipeline output volume.
    """
    result = await _get_published_post_count_text()
    await params.result_callback(result)


async def _get_published_post_count_text() -> str:
    try:
        data = await _worker_get("/api/posts?limit=1")
    except Exception as e:  # noqa: BLE001
        return f"Could not fetch the post count. Error: {e}."
    total = data.get("total", "unknown number of")
    return f"There are {total} published posts on the site."


async def get_ai_spending_status(params: Any) -> None:
    """Get current AI spending — daily and monthly totals across providers.

    Use this when the operator asks about budget, costs, spend, or how
    much money the system has burned today.
    """
    result = await _get_ai_spending_status_text()
    await params.result_callback(result)


async def _get_ai_spending_status_text() -> str:
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


# ---------------------------------------------------------------------------
# Read-only tools added in #voice-tools-expand
# ---------------------------------------------------------------------------


async def _connect_db() -> Any:
    """Open a short-lived asyncpg connection with the standard 2s budget.

    All voice DB tools share this helper so the resolution path (brain
    bootstrap → resolve_database_url) and the timeout stay consistent.
    Caller is responsible for ``await conn.close()`` in a ``finally``.
    """
    import asyncpg
    _ensure_brain_on_path()
    from brain.bootstrap import resolve_database_url
    dsn = resolve_database_url()
    return await asyncpg.connect(dsn, timeout=2.0)


def _shorten(text: str, words: int = 12) -> str:
    """Trim ``text`` to ~``words`` whitespace tokens for voice playback.

    Memory previews and audit details can be paragraphs long. Spoken aloud,
    that's 10-15s of dead air per hit. Caller already builds short summary
    strings; this is the belt-and-suspenders cap so a stray newline-rich
    blob doesn't blow the 15s-per-tool budget.
    """
    parts = (text or "").replace("\n", " ").split()
    if len(parts) <= words:
        return " ".join(parts)
    return " ".join(parts[:words]) + "..."


async def search_memory(params: Any) -> None:
    """Semantic recall over the operator's pgvector memory store.

    Use this when the operator asks what we know about a topic, whether
    a decision was made, if there's a memory of something, or asks
    "remember when". The query is the natural-language phrase to search
    for; up to three relevant snippets are summarised.
    """
    query = _extract_query_arg(params)
    result = await _search_memory_text(query)
    await params.result_callback(result)


def _extract_query_arg(params: Any) -> str:
    """Pull a free-form ``query`` arg from a Pipecat function-call params.

    Pipecat passes tool kwargs through ``params.arguments`` (dict). We
    accept ``query``, ``q``, ``topic``, or ``text`` so the LLM has some
    leeway picking a name. Returns an empty string if nothing matches.
    """
    args = getattr(params, "arguments", None) or {}
    if not isinstance(args, dict):
        return ""
    for k in ("query", "q", "topic", "text", "note", "content"):
        v = args.get(k)
        if isinstance(v, str) and v.strip():
            return v.strip()
    return ""


async def _search_memory_text(query: str) -> str:
    if not query:
        return "I need a phrase to search for. What should I look up?"
    try:
        data = await _worker_get(f"/api/memory/search?q={query}&limit=3")
    except Exception as e:  # noqa: BLE001
        return (
            f"I couldn't reach the memory search. The worker might be down. "
            f"Error: {e}."
        )
    hits = data.get("hits") or []
    if not hits:
        return f"I found nothing in memory about {query}."
    pieces: list[str] = []
    for h in hits[:3]:
        source = h.get("source_table") or "memory"
        preview = _shorten(str(h.get("text_preview") or ""), 12)
        pieces.append(f"from {source}: {preview}")
    return f"Top matches for {query}. " + ". ".join(pieces) + "."


async def list_recent_pipeline_tasks(params: Any) -> None:
    """List the most recent items in the content pipeline.

    Use this when the operator asks what's running, the current pipeline
    state, what tasks are in flight, or what the worker is doing right
    now. Returns a count plus a one-line breakdown by status.
    """
    result = await _list_recent_pipeline_tasks_text()
    await params.result_callback(result)


async def _list_recent_pipeline_tasks_text() -> str:
    # /api/tasks requires auth — fall back to a direct DB read against
    # content_tasks. The voice agent runs in the same process tree as
    # Postgres so a 2s connect budget is generous.
    try:
        conn = await _connect_db()
    except Exception as e:  # noqa: BLE001
        return f"I couldn't reach the database. Error: {e}."
    try:
        rows = await conn.fetch(
            """
            SELECT status
            FROM content_tasks
            ORDER BY created_at DESC
            LIMIT 5
            """,
        )
    except Exception as e:  # noqa: BLE001
        await conn.close()
        return f"I couldn't read the task list. Error: {e}."
    finally:
        try:
            await conn.close()
        except Exception:  # noqa: BLE001
            pass

    if not rows:
        return "There are no recent pipeline tasks."
    counts: dict[str, int] = {}
    for r in rows:
        status = (r["status"] or "unknown").replace("_", " ")
        counts[status] = counts.get(status, 0) + 1
    total = len(rows)
    parts = [f"{n} {status}" for status, n in sorted(counts.items(), key=lambda x: -x[1])]
    return f"Latest {total} pipeline tasks. " + ", ".join(parts) + "."


async def get_audit_summary(params: Any) -> None:
    """Summarise the last 24 hours of audit-log activity.

    Use this when the operator asks if anything's broken, whether there
    are errors, or asks for a quick audit summary. Reports the top three
    event types by count along with their severity.
    """
    result = await _get_audit_summary_text()
    await params.result_callback(result)


async def _get_audit_summary_text() -> str:
    # No /api/audit/summary endpoint exists yet — query audit_log directly,
    # same way _spending_from_db() handles its missing summary endpoint.
    try:
        conn = await _connect_db()
    except Exception as e:  # noqa: BLE001
        return f"I couldn't reach the database. Error: {e}."
    try:
        rows = await conn.fetch(
            """
            SELECT event_type, severity, COUNT(*) AS n
            FROM audit_log
            WHERE timestamp > NOW() - INTERVAL '24 hours'
            GROUP BY event_type, severity
            ORDER BY n DESC
            LIMIT 3
            """,
        )
    except Exception as e:  # noqa: BLE001
        return f"I couldn't read the audit log. Error: {e}."
    finally:
        try:
            await conn.close()
        except Exception:  # noqa: BLE001
            pass

    if not rows:
        return "The audit log is quiet for the last twenty-four hours."
    parts = []
    for r in rows:
        et = (r["event_type"] or "unknown").replace("_", " ")
        sev = r["severity"] or "info"
        parts.append(f"{int(r['n'])} {sev} {et}")
    return "Top audit events in the last day. " + ", ".join(parts) + "."


async def find_similar_posts(params: Any) -> None:
    """Find published posts similar to a topic — duplicate-avoidance check.

    Use this when the operator asks whether we've already covered a topic,
    if a topic would be a duplicate, or wants similar posts about a
    subject. The argument is the topic phrase; up to three closest matches
    are summarised by title.
    """
    topic = _extract_query_arg(params)
    result = await _find_similar_posts_text(topic)
    await params.result_callback(result)


async def _find_similar_posts_text(topic: str) -> str:
    if not topic:
        return "I need a topic to check. What should I compare against?"
    # No /api/posts/similar endpoint — use MemoryClient.find_similar_posts
    # directly. Embedding lookup is fast (single Ollama call) and stays
    # within the 4s tool budget on the local stack.
    _ensure_brain_on_path()
    try:
        from poindexter.memory.client import MemoryClient
    except Exception as e:  # noqa: BLE001
        return f"I couldn't load the memory client. Error: {e}."
    mem = MemoryClient()
    try:
        await mem.connect()
        hits = await mem.find_similar_posts(topic, limit=3, min_similarity=0.5)
    except Exception as e:  # noqa: BLE001
        return f"I couldn't search published posts. Error: {e}."
    finally:
        try:
            await mem.close()
        except Exception:  # noqa: BLE001
            pass

    if not hits:
        return f"No published posts look similar to {topic}."
    parts = []
    for h in hits[:3]:
        title = _shorten(str(h.text_preview or ""), 10)
        parts.append(title)
    return f"Top posts similar to {topic}. " + "; ".join(parts) + "."


async def get_recent_pull_requests(params: Any) -> None:
    """Report recent merged pull requests across the operator's repos.

    Use this when the operator asks what shipped, what got merged, recent
    pull requests, or recent merges. The repo list comes from
    ``app_settings.voice_agent_pr_repos`` (CSV of owner/repo entries) and
    the function reports the three most recent merges across whichever
    repos the operator configured.
    """
    result = await _get_recent_pull_requests_text()
    await params.result_callback(result)


# Repo list now lives in ``app_settings.voice_agent_pr_repos`` (CSV)
# so the public OSS install can ship without leaking Matt's internal
# repo names + operators choose which repos the voice agent reports
# activity for. Default is empty — graceful degradation: an operator
# who hasn't configured this setting hears "no repos configured" from
# the voice tool rather than seeing queries against repos they don't
# own. Closes the leak that #538-540 papered over (per Matt 2026-05-22).
#
# Example seed (operator picks their own repos):
#   poindexter set-setting voice_agent_pr_repos=your-org/repo-one,your-org/repo-two
async def _read_voice_agent_pr_repos(conn: Any) -> list[str]:
    """Read CSV of repo slugs from ``app_settings.voice_agent_pr_repos``.

    Returns an empty list when the setting is unset or empty. Whitespace
    around commas is stripped; blank entries are dropped. The function
    never raises — a DB error surfaces as an empty list and the caller
    degrades to "no repos configured".
    """
    try:
        row = await conn.fetchrow(
            "SELECT value FROM app_settings "
            "WHERE key = 'voice_agent_pr_repos' AND is_active = true "
            "LIMIT 1"
        )
    except Exception:  # noqa: BLE001 — observability, not a contract
        return []
    if row is None:
        return []
    raw = (row["value"] or "").strip()
    if not raw:
        return []
    return [item.strip() for item in raw.split(",") if item.strip()]


async def _get_recent_pull_requests_text() -> str:
    import httpx

    _ensure_brain_on_path()
    try:
        import asyncpg
        from brain.bootstrap import resolve_database_url

        from plugins.secrets import get_secret
        dsn = resolve_database_url()
        pool = await asyncpg.create_pool(dsn, min_size=1, max_size=1, timeout=2.0)
    except Exception as e:  # noqa: BLE001
        return f"I couldn't reach the secrets store. Error: {e}."
    try:
        async with pool.acquire() as conn:
            gh_token = await get_secret(conn, "gh_token") or ""
            repos = await _read_voice_agent_pr_repos(conn)
    finally:
        await pool.close()

    if not repos:
        return (
            "I don't have any repositories configured for the PR list. "
            "Set voice_agent_pr_repos in app_settings to a "
            "comma-separated list of owner-slash-repo entries."
        )

    headers = {
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
        "User-Agent": "poindexter-voice-agent",
    }
    if gh_token:
        headers["Authorization"] = f"Bearer {gh_token}"

    merged: list[dict[str, Any]] = []
    try:
        async with httpx.AsyncClient(timeout=_TOOL_TIMEOUT_SECONDS) as client:
            for repo in repos:
                url = (
                    f"https://api.github.com/repos/{repo}/pulls"
                    "?state=closed&sort=updated&direction=desc&per_page=10"
                )
                resp = await client.get(url, headers=headers)
                if resp.status_code >= 400:
                    continue
                data = resp.json()
                if not isinstance(data, list):
                    continue
                for pr in data:
                    if not isinstance(pr, dict) or not pr.get("merged_at"):
                        continue
                    merged.append({
                        "repo": repo.split("/", 1)[-1],
                        "number": pr.get("number"),
                        "title": pr.get("title", ""),
                        "merged_at": pr.get("merged_at", ""),
                    })
    except Exception as e:  # noqa: BLE001
        return f"I couldn't reach GitHub. Error: {e}."

    if not merged:
        return "No merged pull requests showed up in the last batch."
    merged.sort(key=lambda m: m["merged_at"], reverse=True)
    parts = []
    for pr in merged[:3]:
        title = _shorten(pr["title"], 8)
        parts.append(f"PR {pr['number']} on {pr['repo']}, {title}")
    return "Most recent merged PRs. " + "; ".join(parts) + "."


async def get_brain_decisions(params: Any) -> None:
    """Report the brain daemon's recent non-routine decisions.

    Use this when the operator asks what the brain is doing, whether
    there's been any self-healing activity, or asks for brain status.
    Filters out the routine cycle heartbeats and returns up to three
    real decisions with their reasoning.
    """
    result = await _get_brain_decisions_text()
    await params.result_callback(result)


async def _get_brain_decisions_text() -> str:
    try:
        conn = await _connect_db()
    except Exception as e:  # noqa: BLE001
        return f"I couldn't reach the database. Error: {e}."
    try:
        # Same noise filter as dev_diary_source._collect_brain_decisions:
        # the cycle heartbeat and "Monitored N internal" rows are pure
        # housekeeping and would crowd out anything interesting.
        rows = await conn.fetch(
            """
            SELECT decision, reasoning
            FROM brain_decisions
            WHERE created_at > NOW() - INTERVAL '6 hours'
              AND decision NOT LIKE 'Cycle complete:%%'
              AND decision NOT LIKE 'Monitored %% internal%%'
              AND COALESCE(reasoning, '') NOT LIKE 'Monitored %% internal%%'
            ORDER BY created_at DESC
            LIMIT 3
            """,
        )
    except Exception as e:  # noqa: BLE001
        return f"I couldn't read brain decisions. Error: {e}."
    finally:
        try:
            await conn.close()
        except Exception:  # noqa: BLE001
            pass

    if not rows:
        return "The brain has been quiet for the last six hours."
    parts = []
    for r in rows:
        decision = _shorten(str(r["decision"] or ""), 10)
        parts.append(decision)
    return "Recent brain decisions. " + "; ".join(parts) + "."


# ---------------------------------------------------------------------------
# Input-requiring tools — additive only, no destructive surface
# ---------------------------------------------------------------------------


_VALID_DEV_DIARY_MOODS = frozenset(
    {"slog", "triumph", "flow", "frustrated", "curious", "relief"},
)


async def submit_dev_diary_note(params: Any) -> None:
    """Save an operator note for today's dev diary post.

    Use this when the operator says "save a note that...", "log this for
    today's diary...", "note this...", or otherwise wants something
    captured for the daily diary. The note text is required; an optional
    mood tag (slog, triumph, flow, frustrated, curious, relief) sets the
    emotional through-line.
    """
    args = getattr(params, "arguments", None) or {}
    note = ""
    mood: str | None = None
    if isinstance(args, dict):
        note = str(args.get("note") or args.get("text") or args.get("content") or "").strip()
        raw_mood = args.get("mood")
        if isinstance(raw_mood, str) and raw_mood.strip():
            mood = raw_mood.strip().lower()
    result = await _submit_dev_diary_note_text(note, mood)
    await params.result_callback(result)


async def _submit_dev_diary_note_text(note: str, mood: str | None) -> str:
    if not note:
        return "I need note text to save. What should I write down?"
    if mood is not None and mood not in _VALID_DEV_DIARY_MOODS:
        valid = ", ".join(sorted(_VALID_DEV_DIARY_MOODS))
        return (
            f"That mood isn't one I track. "
            f"Try one of {valid}, or omit the mood entirely."
        )
    try:
        conn = await _connect_db()
    except Exception as e:  # noqa: BLE001
        return f"I couldn't reach the database. Error: {e}."
    try:
        row = await conn.fetchrow(
            """
            INSERT INTO operator_notes (niche_slug, note, mood, created_by)
            VALUES ('dev_diary', $1, $2, 'voice')
            RETURNING id
            """,
            note, mood,
        )
    except Exception as e:  # noqa: BLE001
        return f"I couldn't save the note. Error: {e}."
    finally:
        try:
            await conn.close()
        except Exception:  # noqa: BLE001
            pass

    note_id = int(row["id"]) if row else 0
    snippet = _shorten(note, 10)
    suffix = f" with mood {mood}" if mood else ""
    return f"Saved diary note {note_id}{suffix}. Note reads: {snippet}"


async def store_memory(params: Any) -> None:
    """Save a fact spoken by the operator into the long-term memory store.

    Use this when the operator says "remember that...", "save to memory
    that...", "for the record...", or otherwise wants a fact persisted.
    The content argument is required and is embedded as a memory row
    the operator can later recall via search.
    """
    text = _extract_query_arg(params)
    result = await _store_memory_text(text)
    await params.result_callback(result)


async def _store_memory_text(text: str) -> str:
    if not text:
        return "I need something to remember. What should I save?"
    _ensure_brain_on_path()
    try:
        from poindexter.memory.client import MemoryClient
    except Exception as e:  # noqa: BLE001
        return f"I couldn't load the memory client. Error: {e}."
    mem = MemoryClient()
    try:
        await mem.connect()
        await mem.store(text=text, writer="user", source_table="memory")
    except Exception as e:  # noqa: BLE001
        return f"I couldn't save that to memory. Error: {e}."
    finally:
        try:
            await mem.close()
        except Exception:  # noqa: BLE001
            pass

    snippet = _shorten(text, 12)
    return f"Saved to memory. Note reads: {snippet}"


_DEFAULT_TOOLS: list[Any] = [
    # Existing tools (do not reorder — operator-facing introspection
    # surfaces this list as the canonical voice-tool inventory).
    check_pipeline_health,
    get_published_post_count,
    get_ai_spending_status,
    # New read-only tools, alphabetical.
    find_similar_posts,
    get_audit_summary,
    get_brain_decisions,
    get_recent_pull_requests,
    list_recent_pipeline_tasks,
    search_memory,
    # Input-requiring tools last — additive surface only (per
    # feedback_no_silent_defaults: no destructive voice operations
    # without a separate gate).
    store_memory,
    submit_dev_diary_note,
]


# ---------------------------------------------------------------------------
# LiveKit token helper
# ---------------------------------------------------------------------------


# Default join-JWT TTL for the always-on bot (minutes). Kept as a module
# constant so the mint default and the proactive refresh loop agree on the
# same number — the refresh fires one safety-margin BEFORE this elapses.
_DEFAULT_TOKEN_TTL_MINUTES = 360

# Default safety margin (minutes) the refresh loop subtracts from the TTL
# when scheduling the re-mint. Operators tune it via app_settings key
# ``voice_agent_token_refresh_margin_minutes`` (config-in-DB). Five minutes
# is generous headroom against clock skew + reconnect latency.
_DEFAULT_TOKEN_REFRESH_MARGIN_MINUTES = 5

# Floor for the refresh interval (seconds). Guards against a misconfigured
# margin >= TTL that would otherwise schedule a zero/negative sleep and spin
# the loop in a tight busy-reconnect cycle.
_MIN_TOKEN_REFRESH_INTERVAL_S = 60


def _mint_token(
    api_key: str,
    api_secret: str,
    *,
    identity: str,
    room: str,
    can_publish: bool = True,
    can_subscribe: bool = True,
    ttl_minutes: int = _DEFAULT_TOKEN_TTL_MINUTES,
) -> str:
    """Mint a LiveKit JWT for the given identity + room.

    Thin wrapper around :func:`services.voice_pipecat.mint_livekit_token`
    so the always-on container's ``--print-client-token`` surface keeps
    its existing call shape (minutes, not seconds) while the underlying
    JWT plumbing lives in one place shared with the MCP-side bridge.
    """
    return _shared_mint_livekit_token(
        room=room,
        identity=identity,
        api_key=api_key,
        api_secret=api_secret,
        ttl_s=ttl_minutes * 60,
        can_publish=can_publish,
        can_subscribe=can_subscribe,
    )


def _resolve_livekit_creds(site_config: Any | None = None) -> tuple[str, str, str]:
    """Pull LiveKit URL + API key + secret via the shared resolver.

    Delegates to :func:`services.voice_pipecat.resolve_livekit_creds` so
    the always-on container and the MCP-side bridge read the same
    resolution chain (site_config -> LIVEKIT_URL env -> dev fallback;
    LIVEKIT_API_KEY / LIVEKIT_API_SECRET from env). Kept as a module-level
    name so existing tests that patch ``services.voice_agent_livekit.
    _resolve_livekit_creds`` continue to work.
    """
    return _shared_resolve_livekit_creds(site_config)


async def _resolve_livekit_creds_async(
    site_config: Any | None = None,
) -> tuple[str, str, str]:
    """Async, DB-first creds (#1000) — key/secret from app_settings, env
    fallback. Used by ``run_bot`` (which has a loaded SiteConfig pool). Kept
    as a module-level name so tests can patch it."""
    return await _shared_resolve_livekit_creds_async(site_config)


# ---------------------------------------------------------------------------
# Proactive JWT refresh (#564)
# ---------------------------------------------------------------------------
#
# The always-on bot mints a fixed-TTL join JWT and stays in room
# ``poindexter`` indefinitely. Once that JWT expires, the LiveKit rtc
# ``Room``'s *native* reconnect re-uses the original (now-expired) token and
# gets ``401 Unauthorized - no permissions to access the room`` — the bot
# can't self-recover until the container restart-policy bounces it.
#
# Verified against the installed SDKs: ``pipecat``'s
# ``LiveKitTransportClient`` captures the token once (``self._token``) and
# the rtc ``Room`` ships it to the native FFI engine a single time inside
# ``connect()``. The engine's automatic reconnect re-uses that captured
# token; mutating ``self._token`` afterward is NOT pushed back down. The only
# way to present a fresh token is a controlled reconnect — a fresh
# ``connect()`` handshake carrying a newly-minted JWT.
#
# So we run a background loop that re-mints BEFORE the current token expires
# and drives a controlled disconnect -> reconnect so the bot stays
# authenticated for arbitrarily long sessions. Proactive refresh (vs. just
# raising the TTL) is the durable fix: every token expires eventually, and a
# higher TTL only delays the same 401.


def _resolve_token_refresh_interval_s(
    site_config: Any | None,
    *,
    ttl_minutes: int = _DEFAULT_TOKEN_TTL_MINUTES,
) -> int:
    """Resolve how long to wait before re-minting the join JWT, in seconds.

    The refresh fires one safety-margin BEFORE the TTL elapses so the
    controlled reconnect always presents a still-valid replacement token.
    The margin is DB-configurable via app_settings
    ``voice_agent_token_refresh_margin_minutes`` (config-in-DB per Matt's
    "every tunable lives in app_settings" rule); it defaults to
    :data:`_DEFAULT_TOKEN_REFRESH_MARGIN_MINUTES`.

    A margin >= the TTL (operator typo) would yield a zero/negative sleep
    that spins the loop in a tight busy-reconnect cycle, so the result is
    clamped to :data:`_MIN_TOKEN_REFRESH_INTERVAL_S`.
    """
    margin_minutes = _DEFAULT_TOKEN_REFRESH_MARGIN_MINUTES
    if site_config is not None:
        raw: Any = _DEFAULT_TOKEN_REFRESH_MARGIN_MINUTES
        try:
            raw = site_config.get(
                "voice_agent_token_refresh_margin_minutes",
                _DEFAULT_TOKEN_REFRESH_MARGIN_MINUTES,
            )
            margin_minutes = int(str(raw).strip())
        except (TypeError, ValueError):
            log.warning(
                "voice_agent_token_refresh_margin_minutes=%r is not an int; "
                "using default %d.",
                raw,
                _DEFAULT_TOKEN_REFRESH_MARGIN_MINUTES,
            )
            margin_minutes = _DEFAULT_TOKEN_REFRESH_MARGIN_MINUTES

    interval_s = (ttl_minutes - margin_minutes) * 60
    return max(interval_s, _MIN_TOKEN_REFRESH_INTERVAL_S)


async def _refresh_transport_token(
    transport: Any,
    *,
    api_key: str,
    api_secret: str,
    identity: str,
    room: str,
    ttl_minutes: int = _DEFAULT_TOKEN_TTL_MINUTES,
) -> str:
    """Re-mint a fresh join JWT and reconnect the transport with it.

    The native LiveKit engine only reads the token at ``connect()`` time, so
    the fresh token MUST be installed on the Pipecat client BEFORE the
    reconnect handshake. Sequence:

    1. Mint a brand-new JWT for ``identity`` in ``room``.
    2. Swap ``transport._client._token`` for it.
    3. Drive a controlled reconnect (``disconnect()`` then ``connect()``) so
       the engine re-handshakes carrying the fresh token.

    Returns the freshly-minted token. Tolerates a transport whose ``_client``
    isn't wired yet (Pipecat builds it lazily during ``setup()``); in that
    window we still re-mint so the caller can log progress, and skip the
    reconnect rather than crashing the loop.
    """
    new_token = _mint_token(
        api_key,
        api_secret,
        identity=identity,
        room=room,
        ttl_minutes=ttl_minutes,
    )

    client = getattr(transport, "_client", None)
    if client is None:
        log.warning(
            "Token refresh: transport has no _client yet (setup incomplete?); "
            "minted a fresh JWT but skipping the reconnect this tick.",
        )
        return new_token

    # Install the fresh token first — the engine captures it at connect()
    # time, so a stale value here would reproduce the very 401 we're fixing.
    client._token = new_token

    # Controlled reconnect. The Pipecat client reference-counts connect /
    # disconnect; for the single always-on bot the counter is 1, so one
    # disconnect() actually tears the native connection down and the
    # following connect() re-handshakes with the fresh token. The rtc Room
    # object persists across this cycle (created once in setup()).
    await client.disconnect()
    await client.connect()

    log.info(
        "Refreshed LiveKit JWT and reconnected room %r as %r "
        "(proactive, before TTL expiry).",
        room,
        identity,
    )
    return new_token


async def _token_refresh_loop(
    transport: Any,
    *,
    site_config: Any | None,
    api_key: str,
    api_secret: str,
    identity: str,
    room: str,
    ttl_minutes: int = _DEFAULT_TOKEN_TTL_MINUTES,
) -> None:
    """Background loop: re-mint + reconnect once per (TTL - margin) window.

    Runs concurrently with the Pipecat ``PipelineRunner`` for the lifetime
    of the bot. A single failed refresh (e.g. a transient reconnect error)
    is logged and retried on the next tick rather than propagated — keeping
    the bot online is the whole point of #564. ``CancelledError`` propagates
    so the loop tears down cleanly when ``run_bot`` shuts down.
    """
    while True:
        interval_s = _resolve_token_refresh_interval_s(site_config, ttl_minutes=ttl_minutes)
        await asyncio.sleep(interval_s)
        try:
            await _refresh_transport_token(
                transport,
                api_key=api_key,
                api_secret=api_secret,
                identity=identity,
                room=room,
                ttl_minutes=ttl_minutes,
            )
        except asyncio.CancelledError:
            raise
        except Exception as e:  # noqa: BLE001 — never let one failure kill the loop
            log.warning(
                "Token refresh tick failed (%s); will retry next window. "
                "The bot keeps running on its current token until then.",
                e,
            )


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


_VALID_BRAIN_MODES = ("ollama", "claude-code")
# Fallback when the DB-config brain mode is missing or invalid. A typo in
# app_settings must keep the always-on voice line UP (warn + default), not
# crash-loop it — see _resolve_brain_mode.
_DEFAULT_BRAIN_MODE = "ollama"


# Always-on service profiles (#1006 two-room split). Each profile maps a
# ``--service`` invocation to the app_settings key namespace it reads for
# its enabled-flag / room / identity, plus an optional pinned brain. The
# two-room split runs ONE container per profile:
#
#   default      — the original poindexter room. Emma/GLM ops bot. Brain is
#                  resolved from ``voice_agent_brain_mode`` at pipeline-build
#                  time (``brain=None``) so the start_voice_call MCP tool can
#                  still flip it mid-shift.
#   claude-code  — the dev-on-the-go room. Brain is PINNED to ``claude-code``
#                  (this container IS the dev brain — it must never silently
#                  fall back to ollama if the global brain_mode changes), and
#                  it reads its own ``voice_agent_claude_code_*`` namespace
#                  (which already holds the session/host-brain keys).
#
# Keeping room/identity/enabled in the DB (not compose flags) preserves the
# DB-first config posture: the operator turns the claude-code room on/off
# from a phone via ``voice_agent_claude_code_enabled`` exactly like the
# poindexter room's ``voice_agent_livekit_enabled``.
_SERVICE_PROFILES: dict[str, dict[str, Any]] = {
    "default": {
        "enabled_key": "voice_agent_livekit_enabled",
        "room_key": "voice_agent_room_name",
        "room_default": "poindexter",
        "identity_key": "voice_agent_identity",
        "identity_default": "poindexter-bot",
        "brain": None,  # resolve from voice_agent_brain_mode at build time
    },
    "claude-code": {
        "enabled_key": "voice_agent_claude_code_enabled",
        "room_key": "voice_agent_claude_code_room_name",
        "room_default": "claude-code",
        "identity_key": "voice_agent_claude_code_identity",
        "identity_default": "claude-code-bot",
        "brain": "claude-code",  # pinned — this container IS the dev brain
    },
}


def _coerce_int(raw: Any, default: int) -> int:
    """Coerce an app_settings string to int, falling back to ``default``.

    Guards the auto-reset tunables (#1006): a blank or malformed
    ``voice_agent_claude_code_session_{token_budget,max_age_seconds}`` value
    must not take the surface offline — it logs and uses the documented
    default instead.
    """
    try:
        return int(str(raw).strip())
    except (TypeError, ValueError):
        log.warning(
            "voice-agent: invalid int setting %r — using default %s (#1006)",
            raw, default,
        )
        return default


def _resolve_brain_mode(site_config: Any, override: str | None) -> str:
    """Resolve the voice-agent brain mode for the next pipeline build.

    Resolution order (Half B — runtime brain-mode toggle):

    1. ``override`` — the explicit ``--brain`` CLI flag or the ``brain``
       kwarg passed to :func:`run_bot`. When set, wins outright. An
       invalid override is a caller bug and raises ``SystemExit``.
    2. ``site_config['voice_agent_brain_mode']`` — the canonical (and
       now only) DB key. This is the surface the ``start_voice_call``
       MCP tool flips at runtime so the next pipeline build picks up a
       different brain without bouncing the always-on container.
    3. :data:`_DEFAULT_BRAIN_MODE` — the documented default, used when
       the DB key is empty OR holds an invalid value.

    The DB value is operator-editable, so an invalid one (a typo) is
    logged loudly and falls back to the default rather than crashing:
    a bad app_settings value must never crash-loop the always-on voice
    line (a fat-fingered value did exactly that on 2026-06-04). The
    legacy ``voice_agent_brain`` fallback key was retired here.
    Whitespace and casing are normalised.
    """
    # An explicit override (the --brain CLI flag / the brain kwarg passed by
    # the start_voice_call MCP tool) is a deliberate per-invocation choice, so
    # an invalid one is a caller bug — surface it loudly rather than silently
    # picking something else.
    if override is not None:
        candidate = str(override).strip().lower()
        if candidate not in _VALID_BRAIN_MODES:
            raise SystemExit(
                f"voice brain override={candidate!r} is invalid. "
                f"Valid values: {', '.join(_VALID_BRAIN_MODES)}.",
            )
        return candidate

    # DB-config path (the always-on --service container). This value is
    # operator-editable, so a typo must NOT crash-loop the voice line — a
    # fat-fingered ``voice_agent_brain_mode`` (set to the room name) took the
    # bot down on a restart loop 2026-06-04. Warn loudly and fall back to the
    # documented default so the surface stays up.
    candidate = str(site_config.get("voice_agent_brain_mode", "") or "").strip().lower()
    if not candidate:
        return _DEFAULT_BRAIN_MODE
    if candidate not in _VALID_BRAIN_MODES:
        log.error(
            "voice_agent_brain_mode=%r is invalid (valid: %s) — falling back to "
            "%r so the voice line stays up. Fix the app_settings value.",
            candidate, ", ".join(_VALID_BRAIN_MODES), _DEFAULT_BRAIN_MODE,
        )
        return _DEFAULT_BRAIN_MODE
    return candidate


async def run_bot(
    room: str,
    identity: str = "poindexter-bot",
    *,
    brain: str | None = None,
    project_dir: str | None = None,
) -> None:
    """Join ``room`` as ``identity`` and run the voice pipeline until killed.

    Args:
        room: LiveKit room to join.
        identity: Bot's room identity. Multiple bots in the same room
            need distinct identities.
        brain: Optional override for the LLM stage. ``None`` (default)
            means "read from app_settings at pipeline-build time" —
            ``voice_agent_brain_mode`` first, then the legacy
            ``voice_agent_brain`` key, then the documented default of
            ``"ollama"``. Passing ``"ollama"`` or ``"claude-code"``
            forces that mode regardless of the setting (used by the
            ``--brain`` CLI flag for ad-hoc invocations). Resolving the
            value INSIDE the bot rather than at process start lets the
            ``start_voice_call`` MCP tool flip the brain mid-shift —
            the next call's pipeline build picks up the change without
            bouncing the always-on container.
        project_dir: When the resolved brain is ``"claude-code"``, the
            directory the Claude subprocess runs in. Determines which
            CLAUDE.md is loaded. Defaults to the bot process's cwd.
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
        # DB-first (#1000): key/secret from app_settings, env fallback.
        url, key, secret = await _resolve_livekit_creds_async(_bootstrap_cfg)
    finally:
        await _bootstrap_pool.close()

    token = _mint_token(key, secret, identity=identity, room=room)

    log.info(
        "Joining LiveKit room %r as %r (brain=%s, override=%s) at %s",
        room, identity, brain or "<from-settings>", brain is not None, url,
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

        # Half B: resolve the brain mode AT pipeline-build time, not at
        # process start. This is the runtime toggle surface — flipping
        # ``voice_agent_brain_mode`` via the start_voice_call MCP tool
        # (or `poindexter settings set ...`) takes effect on the next call's
        # pipeline build without restarting the container.
        resolved_brain = _resolve_brain_mode(site_config, brain)
        if brain is None:
            log.info(
                "Brain mode resolved from app_settings: %s "
                "(voice_agent_brain_mode > voice_agent_brain > 'ollama')",
                resolved_brain,
            )

        # Pyroscope continuous profiling (Glad-Labs/poindexter#406).
        # Opt-in via app_settings.enable_pyroscope; ships CPU samples
        # under service="poindexter-voice-livekit" so the LiveKit bot
        # shows up next to the worker / brain / WebRTC bot in the
        # Grafana flame-graph panel. Best-effort — any failure is
        # logged inside setup_pyroscope, never raised.
        try:
            from services.profiling import setup_pyroscope
            setup_pyroscope(
                service_name="poindexter-voice-livekit",
                site_config=site_config,
            )
        except Exception as e:  # noqa: BLE001 — profiling must never block startup
            log.warning("Pyroscope setup failed (livekit): %s", e)

        if resolved_brain == "claude-code":
            import uuid as _uuid

            from services.admin_db import AdminDatabase
            from services.voice_agent_claude_code import ClaudeCodeBridgeLLMService

            extra = os.environ.get("CLAUDE_BOT_EXTRA_ARGS", "").split()

            # Resolve the pinned session id (#1006). Precedence:
            #   env CLAUDE_BOT_SESSION_ID (operator override, kept working)
            #   > app_settings.voice_agent_claude_code_session_id (persisted
            #     across restarts; '' counts as unset)
            #   > a fresh uuid4.
            # When the id did NOT come from a non-empty app_settings value we
            # persist the resolved id back so the next container boot reuses
            # the same on-disk claude session and keeps its context.
            env_sess = os.environ.get("CLAUDE_BOT_SESSION_ID", "").strip()
            stored_sess = (site_config.get("voice_agent_claude_code_session_id", "") or "").strip()
            admin_db = AdminDatabase(pool)

            async def _persist_session_id(new_id: str) -> None:
                await admin_db.set_setting(
                    "voice_agent_claude_code_session_id",
                    new_id,
                    category="voice",
                    description=(
                        "Pinned claude -p voice session for the always-on "
                        "claude-code room (#1006)"
                    ),
                )

            if env_sess:
                sess = env_sess
                persist_resolved = True  # env override — persist so it pins
            elif stored_sess:
                sess = stored_sess
                persist_resolved = False  # already the stored value
            else:
                sess = str(_uuid.uuid4())
                persist_resolved = True  # fresh id — persist it

            if persist_resolved:
                await _persist_session_id(sess)

            # Auto-reset config (#1006) — token budget + max age, DB-driven
            # with sane defaults and a guard against malformed values.
            token_budget = _coerce_int(
                site_config.get("voice_agent_claude_code_session_token_budget", "200000"),
                200000,
            )
            max_age_seconds = _coerce_int(
                site_config.get("voice_agent_claude_code_session_max_age_seconds", "14400"),
                14400,
            )

            # Host-brain mode (#1006): when a daemon URL is configured the turn
            # runs on the HOST (full repo + git + write + all MCP) and this
            # read-only container only does audio. Unset => local subprocess.
            host_brain_url = str(
                site_config.get("voice_agent_claude_code_host_brain_url", "") or "",
            ).strip()
            # Only fetch the (secret) token when host mode is actually on —
            # the get_secret round-trip is a per-restart DB hit we skip in the
            # common local-mode case.
            host_brain_token = ""
            if host_brain_url:
                host_brain_token = (
                    await site_config.get_secret(
                        "voice_agent_claude_code_host_brain_token", "",
                    )
                    or ""
                ).strip()
                if not host_brain_token:
                    # Fail loud (no silent default): the daemon requires a
                    # bearer token, so a URL with no token would 401 every turn.
                    log.warning(
                        "voice-agent: host_brain_url is set but "
                        "voice_agent_claude_code_host_brain_token is empty — the "
                        "host daemon will reject every turn with 401 (#1006).",
                    )

            llm_service = ClaudeCodeBridgeLLMService(
                cwd=project_dir or os.getcwd(),
                extra_args=extra or None,
                session_id=sess,
                token_budget=token_budget,
                max_age_seconds=max_age_seconds,
                persist_session_id=_persist_session_id,
                host_brain_url=host_brain_url or None,
                host_brain_token=host_brain_token or None,
            )
            log.info(
                "Pinned Claude session_id=%s (source=%s, token_budget=%s, "
                "max_age_seconds=%s, brain=%s) — preserves prior turns + auto-resets",
                sess,
                "env" if env_sess else ("app_settings" if stored_sess else "generated"),
                token_budget, max_age_seconds,
                "host" if host_brain_url else "container",
            )
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
                # Let the dev (claude-code) room run a distinct Kokoro
                # voice from the public poindexter room. Empty = fall
                # back to the shared ``voice_agent_tts_voice`` so this
                # is a no-op until an operator sets the override.
                tts_voice_override=site_config.get(
                    "voice_agent_claude_code_tts_voice", "",
                ),
            )
        else:
            task = build_voice_pipeline_task(
                transport, site_config, log=log, tools=_DEFAULT_TOOLS,
            )

        # Proactive JWT refresh (#564). Mint a fresh join token + drive a
        # controlled reconnect one safety-margin BEFORE the current TTL
        # expires, so the bot's native reconnect never re-presents an
        # expired token and 401s. Runs concurrently with the pipeline for
        # the lifetime of the bot; cancelled cleanly on shutdown below.
        refresh_task = asyncio.create_task(
            _token_refresh_loop(
                transport,
                site_config=site_config,
                api_key=key,
                api_secret=secret,
                identity=identity,
                room=room,
            ),
        )

        runner = PipelineRunner(handle_sigint=False)
        try:
            await runner.run(task)
        finally:
            refresh_task.cancel()
            try:
                await refresh_task
            except asyncio.CancelledError:
                pass
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


async def run_service(profile: str = "default") -> int:
    """Start the always-on LiveKit voice bot for one room ``profile``.

    The two-room split (#1006) runs one container per profile (see
    :data:`_SERVICE_PROFILES`):

      - ``default``     — the poindexter room. Brain resolved from
        ``voice_agent_brain_mode`` inside :func:`run_bot` at
        pipeline-build time so the ``start_voice_call`` MCP tool can flip
        it mid-shift without bouncing the container (Half B runtime
        toggle); ``brain`` stays ``None`` here.
      - ``claude-code`` — the dev-on-the-go room. Brain is PINNED to
        ``claude-code`` so a change to the global ``voice_agent_brain_mode``
        (e.g. the poindexter room flipping to ollama) can never silently
        turn this container into an ollama bot.

    Settings read here (keys vary by profile — the ``claude-code`` profile
    reads its own ``voice_agent_claude_code_*`` namespace):
      - enabled flag — if false/0/no/off, exit 0 immediately so docker's
        ``unless-stopped`` policy leaves us stopped without crash-looping
      - room name    — LiveKit room to join
      - identity     — bot identity in the room

    Returns the desired process exit code.
    """
    spec = _SERVICE_PROFILES.get(profile)
    if spec is None:
        # No silent fallback to the default profile — a typo'd profile in
        # the container command must fail loud (feedback_no_silent_defaults).
        raise SystemExit(
            f"--service-profile={profile!r} is invalid. "
            f"Valid values: {', '.join(sorted(_SERVICE_PROFILES))}.",
        )

    _ensure_brain_on_path()
    import asyncpg
    from brain.bootstrap import require_database_url

    from services.site_config import SiteConfig

    dsn = require_database_url(source=f"voice_agent_livekit_service[{profile}]")
    pool = await asyncpg.create_pool(dsn, min_size=1, max_size=2)
    try:
        site_config = SiteConfig()
        await site_config.load(pool)

        enabled = str(
            site_config.get(spec["enabled_key"], "true"),
        ).strip().lower()
        if enabled in {"false", "0", "no", "off"}:
            log.info(
                "%s=%s — voice room %r disabled, exiting 0 so docker leaves "
                "us stopped under unless-stopped.",
                spec["enabled_key"], enabled, profile,
            )
            return 0

        room = str(
            site_config.get(spec["room_key"], spec["room_default"]),
        ).strip() or spec["room_default"]
        identity = str(
            site_config.get(spec["identity_key"], spec["identity_default"]),
        ).strip() or spec["identity_default"]
    finally:
        # ``run_bot`` opens its own pool (it needs the lifecycle to
        # mirror the Pipecat task), so close this one and avoid double
        # use.
        await pool.close()

    # ``brain`` per profile: None ("default") = resolve from app_settings
    # each pipeline build; "claude-code" = pinned. Either way validation
    # lives in `_resolve_brain_mode`, which raises SystemExit on an invalid
    # value, so the loud-fail posture is preserved.
    await run_bot(room, identity, brain=spec["brain"])
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
            "--room/--identity/--brain. Exits 0 if the profile's enabled "
            "flag is false. Pair with --service-profile to pick the room."
        ),
    )
    parser.add_argument(
        "--service-profile", default="default", choices=sorted(_SERVICE_PROFILES),
        help=(
            "Which always-on room profile to run under --service (#1006 "
            "two-room split). 'default' = the poindexter room (brain from "
            "voice_agent_brain_mode); 'claude-code' = the dev-on-the-go room "
            "(brain pinned to claude-code, reads the voice_agent_claude_code_* "
            "key namespace). Ignored without --service. Default: default."
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
        default=None,
        help=(
            "Override the LLM stage for this invocation. Without the flag "
            "(default) the brain is resolved from app_settings at "
            "pipeline-build time — voice_agent_brain_mode first, then "
            "voice_agent_brain (legacy), then 'ollama'. Pass 'ollama' "
            "(snappy local glm-4.7-5090 + read-only tools) or "
            "'claude-code' (shells `claude -p` under the operator's Max "
            "OAuth sub — slower but has full repo / MCP / edit access "
            "for dev-on-the-go) to force a specific mode regardless of "
            "the setting."
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
            rc = asyncio.run(run_service(profile=args.service_profile))
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
