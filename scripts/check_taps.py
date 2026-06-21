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

    print("=== All taps: status + last error ===")
    rows = await pool.fetch("""
        SELECT name, handler_name, enabled, last_run_status, last_run_at,
               last_run_records, total_runs, last_error
        FROM external_taps
        ORDER BY last_run_status DESC, name
    """)
    for r in rows:
        status = r['last_run_status'] or '(never run)'
        err = (r['last_error'] or '')[:200]
        print(f"  {r['name']} ({r['handler_name']}) enabled={r['enabled']}")
        print(f"    status={status} runs={r['total_runs']} records={r['last_run_records']} at={r['last_run_at']}")
        if err:
            print(f"    ERROR: {err}")
        print()

    await pool.close()

asyncio.run(main())
