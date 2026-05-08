"""
Telegram Pipeline Bot — manage Poindexter from Telegram.

Commands:
  /health     — system status
  /tasks      — list posts awaiting approval
  /approve ID — approve a post
  /reject ID  — reject a post
  /stats      — pipeline stats (24h)
  /publish    — queue a new topic

Reads config from app_settings DB (telegram_bot_token, OAuth client
credentials, or legacy api_token). No .env or environment variables
needed.

Authentication (Glad-Labs/poindexter#248):
  Prefers OAuth 2.1 client credentials when ``scripts_oauth_client_id``
  + ``scripts_oauth_client_secret`` are present in app_settings or
  bootstrap.toml. Falls back to the legacy static Bearer
  (``api_token``) when OAuth isn't configured. Run
  ``poindexter auth migrate-scripts`` to provision the OAuth path.

Usage:
    python scripts/telegram-bot.py
"""

import asyncio
import os
import sys
from pathlib import Path

import asyncpg
import httpx

_project_root = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_project_root))
sys.path.insert(0, str(_project_root / "scripts"))
# `services.*` lives under src/cofounder_agent/ — add that to sys.path
# so we can import the integrations package without restructuring.
sys.path.insert(0, str(_project_root / "src" / "cofounder_agent"))

# OAuth helper lives next to this script — see scripts/_oauth_helper.py
# for the module-level docstring on resolution order. We hold a single
# pool + client at module scope so every command shares the cached JWT
# instead of minting per request.
from _oauth_helper import oauth_client_from_pool  # noqa: E402


API_URL = os.getenv("POINDEXTER_API_URL", "http://localhost:8002")


def _resolve_db_url() -> str:
    from brain.bootstrap import resolve_database_url

    db_url = os.getenv("DATABASE_URL") or resolve_database_url()
    if not db_url:
        print("ERROR: No database URL. Run `poindexter setup` first.")
        sys.exit(1)
    return db_url


async def _load_telegram_config(pool) -> dict:
    """Pull Telegram-specific config from app_settings.

    Goes through ``plugins.secrets.get_secret`` so encrypted rows
    (``is_secret=true`` / ``enc:v1:<base64>`` envelope) get decrypted
    properly. Reading raw ``SELECT value`` would hand the bot the
    ciphertext as if it were the token — same shape as the
    poindexter#185 voice-bot bug + Glad-Labs/poindexter#342 brain bug.

    OAuth creds are NOT fetched here — the OAuth helper handles its own
    resolution (bootstrap.toml + app_settings + decryption).
    """
    from plugins.secrets import get_secret

    async with pool.acquire() as conn:
        token = await get_secret(conn, "telegram_bot_token")
        chat_id = await get_secret(conn, "telegram_chat_id")
    return {
        "telegram_bot_token": token or "",
        "telegram_chat_id": chat_id or "",
    }


print("[INIT] Loading config + OAuth client...")

_DB_URL = _resolve_db_url()


# asyncpg.create_pool() returns a Pool wrapped in __await__ magic, not a
# coroutine — asyncio.run() rejects it as "a coroutine was expected".
# Wrap in a coro so asyncio.run accepts it. Same fix in discord-voice-bot.py.
async def _make_pool() -> asyncpg.Pool:
    return await asyncpg.create_pool(_DB_URL, min_size=1, max_size=2)


# Module-level placeholders — populated in main() under a SINGLE event loop
# so asyncpg's connection pool stays attached to the same loop poll_updates()
# runs in. Earlier this file called asyncio.run() three times during module
# import, which created a fresh loop per call and left _pool attached to a
# dead loop the moment poll_updates() opened the actual run loop. Same
# multi-event-loop landmine as scripts/discord-voice-bot.py + brain_daemon
# (Glad-Labs/poindexter#185 + #344). Setup happens in main() now.
_pool: asyncpg.Pool | None = None
_tg_cfg: dict = {}
_oauth_client = None

BOT_TOKEN = ""
CHAT_ID = ""
TG_API = ""

_last_update_id = 0


async def _setup() -> None:
    """Initialise pool + config + OAuth client under the run loop."""
    global _pool, _tg_cfg, _oauth_client, BOT_TOKEN, CHAT_ID, TG_API
    _pool = await _make_pool()
    _tg_cfg = await _load_telegram_config(_pool)
    _oauth_client = await oauth_client_from_pool(_pool, base_url=API_URL)
    # .strip() guards against trailing whitespace / newlines that crept
    # in during SQL inserts of the secret. urllib rejects URLs with raw
    # \n characters, which would otherwise make every getUpdates call
    # raise "Invalid non-printable ASCII character in URL" forever.
    BOT_TOKEN = _tg_cfg.get("telegram_bot_token", "").strip()
    CHAT_ID = _tg_cfg.get("telegram_chat_id", "").strip()
    TG_API = f"https://api.telegram.org/bot{BOT_TOKEN}"


async def send_message(text: str, chat_id: str = ""):
    """Send a message via Telegram Bot API."""
    async with httpx.AsyncClient(timeout=15) as client:
        await client.post(f"{TG_API}/sendMessage", json={
            "chat_id": chat_id or CHAT_ID,
            "text": text,
            "parse_mode": "Markdown",
        })


# ---------------------------------------------------------------------------
# /cli passthrough bridge
# ---------------------------------------------------------------------------
#
# The passthrough module (services/integrations/telegram_cli_passthrough.py)
# expects a SiteConfig-like object with a sync `.get(key, default)`
# method. The bot already loaded the four /cli telegram_cli_* keys plus
# telegram_chat_id from app_settings at startup; wrap that dict as a
# SiteConfig stand-in so we don't have to drag in the full FastAPI DI.

class _BotSiteConfig:
    """Read-through view over the dict loaded from app_settings.

    Falls back to live DB reads on cache miss so app_settings updates
    made elsewhere (Grafana, MCP `set_setting`, etc.) take effect on
    the bot's next /cli call without a restart.
    """

    def __init__(self, initial: dict[str, str]):
        self._cache = dict(initial)

    def get(self, key: str, default: str = "") -> str:
        if key in self._cache:
            return self._cache.get(key) or default
        # Lazy DB lookup for keys not in initial seed — best-effort.
        try:
            import asyncpg  # noqa: F401  # imported here so a missing dep
                                          # doesn't break the simple paths
            from brain.bootstrap import resolve_database_url
            dsn = os.getenv("DATABASE_URL") or resolve_database_url()
            if dsn:
                async def _fetch_one():
                    conn = await asyncpg.connect(dsn)
                    try:
                        return await conn.fetchval(
                            "SELECT value FROM app_settings WHERE key = $1",
                            key,
                        )
                    finally:
                        await conn.close()

                value = asyncio.get_event_loop().run_until_complete(_fetch_one())
                if value is not None:
                    self._cache[key] = value
                    return value
        except Exception:
            pass
        return default


_PASSTHROUGH_KEYS = (
    "telegram_chat_id",
    "telegram_cli_enabled",
    "telegram_cli_safe_commands",
    "telegram_cli_max_output_chars",
    "telegram_cli_timeout_seconds",
    "telegram_cli_audit_logged",
)


def _load_passthrough_config(initial: dict[str, str]) -> dict[str, str]:
    """Pull the /cli keys at startup so the first invocation isn't slow."""
    extra: dict[str, str] = {}
    try:
        import asyncpg
        from brain.bootstrap import resolve_database_url
        dsn = os.getenv("DATABASE_URL") or resolve_database_url()
        if not dsn:
            return extra

        async def _fetch():
            conn = await asyncpg.connect(dsn)
            try:
                rows = await conn.fetch(
                    "SELECT key, value FROM app_settings WHERE key = ANY($1::text[])",
                    list(_PASSTHROUGH_KEYS),
                )
                return {r["key"]: r["value"] for r in rows}
            finally:
                await conn.close()

        extra = asyncio.run(_fetch())
    except Exception as e:
        print(f"[CLI] Could not preload passthrough settings: {e}")
    return extra


_passthrough_extra = _load_passthrough_config(_tg_cfg)
_BOT_SITE_CONFIG = _BotSiteConfig({**_tg_cfg, **_passthrough_extra})


async def _maybe_handle_cli(text: str, chat_id: str) -> str | None:
    """Route /cli messages to the passthrough; return reply text or None."""
    try:
        from services.integrations.telegram_cli_passthrough import handle_cli_message
    except Exception as e:
        print(f"[CLI] Passthrough import failed: {e}")
        return None

    reply = await handle_cli_message(
        text,
        chat_id,
        site_config=_BOT_SITE_CONFIG,
        # No audit_logger from this standalone script; the worker can
        # wire one in if it ever embeds the passthrough directly.
        audit_logger=None,
    )
    return reply.text if reply is not None else None


class APIError(RuntimeError):
    """Raised when the Poindexter worker returns a non-2xx response.

    Bubbles up through ``handle_command`` to the top-level catch in
    ``poll_updates`` so the operator sees the real error in Telegram
    instead of a misleading "No posts awaiting approval" / "Task: ?"
    that earlier silently masked 401s. (Fixed after the
    ``scripts_oauth_client_id`` provisioning bug surfaced exactly that
    failure mode.)
    """


async def api_call(method: str, path: str, json_data: dict | None = None) -> dict:
    """Make an authenticated API call to the Poindexter worker.

    Auth is handled by the shared ScriptsOAuthClient (OAuth JWT when
    configured, legacy static Bearer otherwise). The 401-retry dance
    happens transparently inside the helper.

    Raises ``APIError`` on any non-2xx so callers don't have to
    error-check every ``data.get()`` themselves.
    """
    assert _oauth_client is not None, "OAuth client not initialised — _setup() must run before api_call"
    if method == "GET":
        resp = await _oauth_client.get(path)
    else:
        resp = await _oauth_client.post(path, json=json_data or {})
    if resp.status_code >= 400:
        # Surface the error verbatim — handlers used to swallow this
        # into a benign default and the operator never saw the real
        # cause. e.g. 401 → bot replied "Task: ?" with no hint that
        # auth was the actual problem.
        raise APIError(
            f"{method} {path} → HTTP {resp.status_code}: {resp.text[:300]}"
        )
    try:
        return resp.json()
    except Exception:
        # 2xx with non-JSON body — also unusual, surface it.
        raise APIError(
            f"{method} {path} → HTTP {resp.status_code} non-JSON response: "
            f"{resp.text[:300]}"
        ) from None


async def handle_command(text: str, chat_id: str):
    """Process a command and send the response."""
    # /cli <args> passthrough — runs the full poindexter CLI surface.
    # Routed BEFORE the legacy slash-command dispatch so a future
    # `/cli` overlap with a one-off command can't be accidentally
    # shadowed. The passthrough returns None for non-/cli messages so
    # this is a safe early-exit.
    cli_reply = await _maybe_handle_cli(text, chat_id)
    if cli_reply is not None:
        await send_message(cli_reply, chat_id)
        return

    parts = text.strip().split()
    cmd = parts[0].lower().replace("@", "").split("@")[0]
    args = parts[1:]

    if cmd == "/health":
        data = await api_call("GET", "/api/health")
        te = data.get("components", {}).get("task_executor", {})
        msg = (
            f"*System:* {data.get('status', '?')}\n"
            f"*Pending:* {te.get('pending_task_count', '?')} | "
            f"*In-progress:* {te.get('in_progress_count', '?')}"
        )
        await send_message(msg, chat_id)

    elif cmd == "/tasks":
        data = await api_call("GET", "/api/tasks?status=awaiting_approval")
        task_list = data if isinstance(data, list) else data.get("tasks", [])
        if not task_list:
            await send_message("No posts awaiting approval.", chat_id)
            return
        lines = []
        for t in task_list[:10]:
            tid = str(t.get("id", "?"))[:8]
            title = t.get("title", t.get("topic", "untitled"))[:45]
            score = t.get("quality_score", "?")
            lines.append(f"`{tid}` Q:{score} {title}")
        await send_message(f"*Awaiting ({len(task_list)}):*\n" + "\n".join(lines), chat_id)

    elif cmd == "/approve" and args:
        # Stage only — does NOT publish. Per feedback_approve_does_not_mean_publish:
        # picking the best N from awaiting_approval is staging, not shipping.
        # Use /publish <id> to actually push live, or /approve-publish for one-step.
        task_id = args[0]
        data = await api_call("POST", f"/api/tasks/{task_id}/approve", {"approved": True})
        status = data.get("status", data.get("error", "?"))
        if status == "approved":
            await send_message(f"#{task_id}: *staged*\nReply `/publish {task_id}` to ship.", chat_id)
        else:
            await send_message(f"#{task_id}: *{status}*", chat_id)

    elif cmd == "/approve-publish" and args:
        # One-step approve + ship. Explicit opt-in to publishing — only use when you've
        # already reviewed the post and want to skip the staging gate.
        task_id = args[0]
        data = await api_call(
            "POST",
            f"/api/tasks/{task_id}/approve",
            {"approved": True, "auto_publish": True},
        )
        status = data.get("status", data.get("error", "?"))
        await send_message(f"#{task_id}: *{status}*", chat_id)

    elif cmd == "/publish" and args:
        task_id = args[0]
        data = await api_call("POST", f"/api/tasks/{task_id}/publish")
        status = data.get("status", data.get("error", "?"))
        await send_message(f"#{task_id}: *{status}*", chat_id)

    elif cmd == "/reject" and args:
        task_id = args[0]
        reason = " ".join(args[1:]) or "Rejected via Telegram"
        data = await api_call("POST", f"/api/tasks/{task_id}/reject", {
            "feedback": reason, "reason": "operator"
        })
        status = data.get("status", data.get("error", "?"))
        await send_message(f"#{task_id}: *{status}* — {reason}", chat_id)

    elif cmd == "/stats":
        try:
            import asyncpg
            from brain.bootstrap import resolve_database_url
            dsn = os.getenv("DATABASE_URL") or resolve_database_url()
            conn = await asyncpg.connect(dsn)
            rows = await conn.fetch(
                "SELECT status, COUNT(*) as c FROM pipeline_tasks_view "
                "WHERE created_at > NOW() - INTERVAL '24 hours' GROUP BY status ORDER BY c DESC"
            )
            total = await conn.fetchval("SELECT COUNT(*) FROM posts WHERE status = 'published'")
            await conn.close()
            lines = [f"*{r['status']}:* {r['c']}" for r in rows]
            await send_message(
                f"*Pipeline (24h):*\n" + "\n".join(lines) + f"\n\n*Total published:* {total}",
                chat_id,
            )
        except Exception as e:
            await send_message(f"Stats error: {e}", chat_id)

    elif cmd == "/publish" and args:
        topic = " ".join(args)
        # Mark as operator-seeded so the off-brand gate (task_executor.py)
        # exempts it — Matt typed it on Telegram, that's good enough.
        data = await api_call(
            "POST",
            "/api/tasks",
            {
                "topic": topic,
                "category": "technology",
                "metadata": {"discovered_by": "operator_telegram"},
            },
        )
        tid = data.get("id", data.get("task_id", "?"))
        await send_message(f"Queued: *{topic}*\nTask: `{tid}`", chat_id)

    elif cmd == "/help" or cmd == "/start":
        await send_message(
            "*Poindexter Pipeline Bot*\n\n"
            "/health — system status\n"
            "/tasks — posts awaiting approval\n"
            "/approve ID — publish a post\n"
            "/reject ID reason — reject a post\n"
            "/stats — pipeline stats (24h)\n"
            "/publish topic — queue a new topic\n"
            "/cli <args> — run any poindexter CLI command (allowlisted)\n",
            chat_id,
        )


async def poll_updates():
    """Long-poll for Telegram updates."""
    global _last_update_id
    async with httpx.AsyncClient(timeout=35) as client:
        while True:
            try:
                resp = await client.get(
                    f"{TG_API}/getUpdates",
                    params={"offset": _last_update_id + 1, "timeout": 30},
                )
                if resp.status_code != 200:
                    await asyncio.sleep(5)
                    continue

                data = resp.json()
                for update in data.get("result", []):
                    _last_update_id = update["update_id"]
                    msg = update.get("message", {})
                    text = msg.get("text", "")
                    chat_id = str(msg.get("chat", {}).get("id", ""))

                    print(f"[POLL] update={update['update_id']} chat={chat_id} text={text[:60]!r}", flush=True)

                    if text.startswith("/") and chat_id == CHAT_ID:
                        print(f"[CMD] dispatching {text[:30]!r}", flush=True)
                        try:
                            await handle_command(text, chat_id)
                            print(f"[CMD] dispatched ok", flush=True)
                        except Exception as e:
                            print(f"[CMD] ERROR: {type(e).__name__}: {e}", flush=True)
                            await send_message(f"Error: {e}", chat_id)
                    elif text.startswith("/"):
                        print(f"[POLL] skipped — chat_id mismatch (got {chat_id!r}, want {CHAT_ID!r})", flush=True)

            except httpx.TimeoutException:
                continue
            except Exception as e:
                print(f"[POLL] Error: {e}")
                await asyncio.sleep(5)


async def _main() -> None:
    await _setup()
    if not BOT_TOKEN:
        print("ERROR: telegram_bot_token not in app_settings.")
        sys.exit(1)
    if not CHAT_ID:
        print("ERROR: telegram_chat_id not in app_settings.")
        sys.exit(1)
    print("[BOT] Telegram Pipeline Bot starting...")
    print(f"[BOT] Listening for commands from chat {CHAT_ID}")
    await poll_updates()


if __name__ == "__main__":
    asyncio.run(_main())
