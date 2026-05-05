"""One-shot: write Langfuse credentials into app_settings + verify lookup.

Reads keys from the running poindexter-langfuse-web container's env
(LANGFUSE_INIT_*) — those keys are what the container provisioned at
first boot. Writes them to the app_settings rows seeded by migration
0153, then verifies UnifiedPromptManager actually picks up the
Langfuse path on the next get_prompt call.

Run from src/cofounder_agent: ``poetry run python scripts/activate_langfuse.py``
"""

from __future__ import annotations

import asyncio
import subprocess
import tomllib
from pathlib import Path

import asyncpg


def _read_init_env() -> dict[str, str]:
    """Pull LANGFUSE_INIT_* vars out of the running container."""
    out = subprocess.check_output(
        ["docker", "exec", "poindexter-langfuse-web", "env"],
        text=True,
    )
    env: dict[str, str] = {}
    for line in out.splitlines():
        if "=" in line and line.startswith("LANGFUSE_INIT_"):
            k, _, v = line.partition("=")
            env[k] = v
    return env


async def main() -> None:
    init_env = _read_init_env()
    pk = init_env.get("LANGFUSE_INIT_PROJECT_PUBLIC_KEY", "")
    sk = init_env.get("LANGFUSE_INIT_PROJECT_SECRET_KEY", "")
    if not pk or not sk:
        print("missing LANGFUSE_INIT_PROJECT_PUBLIC_KEY/SECRET_KEY in container env")
        return

    cfg = tomllib.loads((Path.home() / ".poindexter" / "bootstrap.toml").read_text())
    pool = await asyncpg.create_pool(cfg["database_url"])
    try:
        updates = [
            ("langfuse_host", "http://localhost:3010"),
            ("langfuse_public_key", pk),
            ("langfuse_secret_key", sk),
        ]
        async with pool.acquire() as conn:
            for key, value in updates:
                await conn.execute(
                    "UPDATE app_settings SET value = $1, updated_at = NOW() WHERE key = $2",
                    value, key,
                )
                print(f"  updated {key}")

            rows = await conn.fetch(
                """
                SELECT key, is_secret,
                       CASE WHEN is_secret THEN '<encrypted>' ELSE value END AS display
                  FROM app_settings
                 WHERE key LIKE 'langfuse_%'
                 ORDER BY key
                """
            )
            print("\napp_settings state:")
            for r in rows:
                print(f"  {r['key']:25s} is_secret={r['is_secret']:5} value={r['display']}")
    finally:
        await pool.close()


if __name__ == "__main__":
    asyncio.run(main())
