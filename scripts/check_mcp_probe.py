import asyncio
import os
import sys
import urllib.error
import urllib.request
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

PROBE_PATHS = [
    "http://localhost:8002/health",
    "http://localhost:8002/",
    "http://localhost:8002/api/health",
    "http://localhost:8004/health",
    "http://localhost:8004/",
    "http://localhost:8004/.well-known/oauth-protected-resource",
]

async def main():
    pool = await asyncpg.create_pool(DB_URL)

    print("=== mcp_http_probe settings ===")
    rows = await pool.fetch("""
        SELECT key, value FROM app_settings
        WHERE key LIKE '%mcp_http%' OR key LIKE '%mcp_probe%'
        ORDER BY key
    """)
    for r in rows:
        print(f"  {r['key']} = {r['value']!r}")

    print("\n=== Probing candidate MCP endpoints ===")
    for url in PROBE_PATHS:
        try:
            req = urllib.request.Request(url, headers={"Accept": "application/json"})
            with urllib.request.urlopen(req, timeout=3) as r:
                print(f"  {r.status} {url}")
        except urllib.error.HTTPError as e:
            print(f"  {e.code} {url}")
        except Exception as e:
            print(f"  ERR {url}: {type(e).__name__}: {e}")

    await pool.close()

asyncio.run(main())
