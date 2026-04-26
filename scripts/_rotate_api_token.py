"""One-off rotation script — feat/rotate-api-token.

Reads new token from a 0600 file, writes it encrypted to
app_settings.api_token via plugins.secrets.set_secret, and flips
development_mode to 'false' so the dev-token bypass is dormant.

Usage:
    POINDEXTER_SECRET_KEY=<key> python scripts/_rotate_api_token.py <token-file>

Never echoes the token. Safe to commit (it doesn't contain a secret).
"""

from __future__ import annotations

import asyncio
import os
import sys
from pathlib import Path

import asyncpg

# Make src/cofounder_agent importable
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src" / "cofounder_agent"))

from plugins.secrets import set_secret  # noqa: E402


async def _main(token_path: str) -> None:
    if not os.getenv("POINDEXTER_SECRET_KEY"):
        print("ERROR: POINDEXTER_SECRET_KEY env var must be set", file=sys.stderr)
        sys.exit(2)

    new_token = Path(token_path).read_text().strip()
    if not new_token or len(new_token) < 32:
        print("ERROR: token file empty or too short", file=sys.stderr)
        sys.exit(2)

    dsn = os.getenv(
        "DATABASE_URL",
        "postgresql://poindexter:poindexter-brain-local@localhost:15432/poindexter_brain",
    )
    conn = await asyncpg.connect(dsn)
    try:
        await set_secret(
            conn,
            "api_token",
            new_token,
            description="API Bearer token for the Poindexter worker. Rotated via feat/rotate-api-token.",
        )
        # Flip development_mode off so the dev-token bypass is dormant.
        # Plain UPDATE — development_mode is not a secret row.
        await conn.execute(
            "UPDATE app_settings SET value = 'false', updated_at = NOW() "
            "WHERE key = 'development_mode'"
        )

        # Verification reads (no plaintext printed)
        row = await conn.fetchrow(
            "SELECT length(value) AS l, is_secret, value LIKE 'enc:v1:%%' AS encrypted "
            "FROM app_settings WHERE key = 'api_token'"
        )
        dm = await conn.fetchval(
            "SELECT value FROM app_settings WHERE key = 'development_mode'"
        )
        print(
            f"api_token: stored_len={row['l']} is_secret={row['is_secret']} "
            f"encrypted={row['encrypted']}"
        )
        print(f"development_mode: {dm}")
    finally:
        await conn.close()


if __name__ == "__main__":
    if len(sys.argv) != 2:
        print("usage: _rotate_api_token.py <token-file>", file=sys.stderr)
        sys.exit(2)
    asyncio.run(_main(sys.argv[1]))
