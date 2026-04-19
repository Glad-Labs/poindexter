"""
One-shot script to clear stale global slash commands from Discord bots.

When multiple bots register the same command names globally, Discord shows
duplicates and neither responds. This clears global commands so only
guild-specific (instant) registrations remain.

Usage:
    python scripts/discord-cleanup-commands.py
"""

import asyncio
import os
import sys
from pathlib import Path

_project_root = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_project_root))


async def main():
    import asyncpg
    from brain.bootstrap import resolve_database_url
    import httpx

    db_url = os.getenv("DATABASE_URL") or resolve_database_url()
    conn = await asyncpg.connect(db_url)
    rows = await conn.fetch(
        "SELECT key, value FROM app_settings WHERE key IN "
        "('discord_bot_token', 'discord_voice_bot_token')"
    )
    await conn.close()
    tokens = {r["key"]: r["value"] for r in rows}

    base = "https://discord.com/api/v10"

    async with httpx.AsyncClient(timeout=30) as client:
        for label, key in [("OpenClaw", "discord_bot_token"), ("Voice", "discord_voice_bot_token")]:
            token = tokens.get(key, "")
            if not token:
                print(f"[{label}] No token found for {key}, skipping")
                continue

            headers = {"Authorization": f"Bot {token}"}

            me = await client.get(f"{base}/users/@me", headers=headers)
            if me.status_code != 200:
                print(f"[{label}] Auth failed ({me.status_code}): {me.text[:100]}")
                continue
            app_id = me.json()["id"]
            bot_name = me.json().get("username", "?")
            print(f"[{label}] Bot: {bot_name} (app_id={app_id})")

            resp = await client.get(
                f"{base}/applications/{app_id}/commands", headers=headers
            )
            if resp.status_code != 200:
                print(f"  Failed to list global commands: {resp.status_code}")
                continue

            cmds = resp.json()
            print(f"  Found {len(cmds)} global command(s)")

            for cmd in cmds:
                name = cmd["name"]
                cmd_id = cmd["id"]
                del_resp = await client.delete(
                    f"{base}/applications/{app_id}/commands/{cmd_id}",
                    headers=headers,
                )
                status = "deleted" if del_resp.status_code == 204 else f"error {del_resp.status_code}"
                print(f"  /{name} ({cmd_id}): {status}")

            if cmds:
                print(f"  Cleared {len(cmds)} global commands from {bot_name}")
            else:
                print(f"  No global commands to clear")

    print("\nDone. Restart the voice bot to re-register guild-specific commands.")


if __name__ == "__main__":
    asyncio.run(main())
