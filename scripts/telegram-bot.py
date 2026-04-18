"""
Telegram Pipeline Bot — manage Poindexter from Telegram.

Commands:
  /health     — system status
  /tasks      — list posts awaiting approval
  /approve ID — approve a post
  /reject ID  — reject a post
  /stats      — pipeline stats (24h)
  /publish    — queue a new topic

Reads config from app_settings DB (telegram_bot_token, api_token).
No .env or environment variables needed.

Usage:
    python scripts/telegram-bot.py
"""

import asyncio
import os
import sys
from pathlib import Path
from urllib.parse import quote

import httpx

_project_root = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_project_root))


def _load_config() -> dict:
    import asyncpg
    from brain.bootstrap import resolve_database_url

    db_url = os.getenv("DATABASE_URL") or resolve_database_url()
    if not db_url:
        print("ERROR: No database URL. Run `poindexter setup` first.")
        sys.exit(1)

    async def _fetch():
        conn = await asyncpg.connect(db_url)
        try:
            rows = await conn.fetch(
                "SELECT key, value FROM app_settings WHERE key IN "
                "('telegram_bot_token', 'telegram_chat_id', 'api_token')"
            )
            return {r["key"]: r["value"] for r in rows}
        finally:
            await conn.close()

    return asyncio.run(_fetch())


print("[INIT] Loading config from app_settings...")
_cfg = _load_config()

BOT_TOKEN = _cfg.get("telegram_bot_token", "")
CHAT_ID = _cfg.get("telegram_chat_id", "")
API_TOKEN = _cfg.get("api_token", "")
API_URL = "http://localhost:8002"
TG_API = f"https://api.telegram.org/bot{BOT_TOKEN}"

_last_update_id = 0


async def send_message(text: str, chat_id: str = ""):
    """Send a message via Telegram Bot API."""
    async with httpx.AsyncClient(timeout=15) as client:
        await client.post(f"{TG_API}/sendMessage", json={
            "chat_id": chat_id or CHAT_ID,
            "text": text,
            "parse_mode": "Markdown",
        })


async def api_call(method: str, path: str, json_data: dict | None = None) -> dict:
    """Make an authenticated API call to the Poindexter worker."""
    async with httpx.AsyncClient(timeout=30) as client:
        headers = {"Authorization": f"Bearer {API_TOKEN}", "Content-Type": "application/json"}
        if method == "GET":
            resp = await client.get(f"{API_URL}{path}", headers=headers)
        else:
            resp = await client.post(f"{API_URL}{path}", headers=headers, json=json_data or {})
        try:
            return resp.json()
        except Exception:
            return {"error": resp.text[:200]}


async def handle_command(text: str, chat_id: str):
    """Process a command and send the response."""
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
        task_id = args[0]
        data = await api_call("POST", f"/api/tasks/{task_id}/approve", {"approved": True})
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
        data = await api_call("POST", "/api/tasks", {"topic": topic, "category": "technology"})
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
            "/publish topic — queue a new topic\n",
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

                    if text.startswith("/") and chat_id == CHAT_ID:
                        try:
                            await handle_command(text, chat_id)
                        except Exception as e:
                            await send_message(f"Error: {e}", chat_id)

            except httpx.TimeoutException:
                continue
            except Exception as e:
                print(f"[POLL] Error: {e}")
                await asyncio.sleep(5)


if __name__ == "__main__":
    if not BOT_TOKEN:
        print("ERROR: telegram_bot_token not in app_settings.")
        sys.exit(1)
    if not CHAT_ID:
        print("ERROR: telegram_chat_id not in app_settings.")
        sys.exit(1)

    print(f"[BOT] Telegram Pipeline Bot starting...")
    print(f"[BOT] Listening for commands from chat {CHAT_ID}")
    asyncio.run(poll_updates())
