import asyncio
import os
import sys
from pathlib import Path

import asyncpg


def _resolve_db_url() -> str:
    """Resolve the brain DSN from bootstrap (bootstrap.toml is canonical, #198)
    and force IPv4. On Windows ``localhost`` resolves to ``::1`` first, where the
    Docker Desktop IPv6 port-proxy silently drops connections; ``127.0.0.1``
    lands on the host postgres. Mirrors ``scripts/gpu-scraper.py`` (#1796).
    """
    dsn = os.getenv("DATABASE_URL")
    if not dsn:
        sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
        try:
            from brain.bootstrap import resolve_database_url  # type: ignore

            dsn = resolve_database_url()
        except Exception as exc:  # bootstrap is best-effort on the host
            print(f"[dsn] bootstrap resolution failed ({exc}); using default", file=sys.stderr)
            dsn = None
    if not dsn:
        dsn = "postgresql://poindexter:poindexter-brain-local@localhost:5433/poindexter_brain"
    return dsn.replace("@localhost:", "@127.0.0.1:")


DB_URL = _resolve_db_url()

async def main():
    pool = await asyncpg.create_pool(DB_URL)

    print("=== video model settings ===")
    rows = await pool.fetch("""
        SELECT key, value FROM app_settings
        WHERE key LIKE '%video_slideshow%'
           OR key LIKE '%cost_tier.standard%'
           OR key LIKE '%cost_tier.budget%'
           OR key LIKE '%pipeline_writer_model%'
        ORDER BY key
    """)
    for r in rows:
        print(f"  {r['key']} = {r['value']!r}")

    await pool.close()

asyncio.run(main())
