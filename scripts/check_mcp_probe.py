import asyncio
import urllib.request

import asyncpg

DB_URL = "postgresql://poindexter:poindexter-brain-local@localhost:15432/poindexter_brain"

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
