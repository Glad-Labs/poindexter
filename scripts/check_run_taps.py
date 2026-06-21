import asyncio
import json
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

    # Check findings by id (findings table)
    print("=== findings table columns ===")
    cols = await pool.fetch("""
        SELECT column_name, data_type
        FROM information_schema.columns
        WHERE table_name = 'findings'
        ORDER BY ordinal_position
    """)
    for c in cols:
        print(f"  {c['column_name']} ({c['data_type']})")

    print("\n=== Target findings (id 73639 / 72593) ===")
    try:
        rows = await pool.fetch(
            "SELECT * FROM findings WHERE id IN (73639, 72593) ORDER BY id"
        )
        for r in rows:
            print(dict(r))
    except Exception as e:
        print(f"  findings query failed: {e}")

    # Check recent run_taps failures in audit_log
    print("\n=== run_taps in audit_log ===")
    rows2 = await pool.fetch("""
        SELECT id, event_type, source, details, severity, timestamp
        FROM audit_log
        WHERE details::text ILIKE '%run_taps%'
           OR source ILIKE '%tap%'
        ORDER BY timestamp DESC
        LIMIT 10
    """)
    for r in rows2:
        d = r['details']
        if isinstance(d, str):
            try:
                d = json.loads(d)
            except Exception:
                pass
        print(f"  [{r['timestamp']}] id={r['id']} source={r['source']} event={r['event_type']}")
        print(f"    {str(d)[:300]}")

    # Check plugin_job_last_status for run_taps
    print("\n=== run_taps app_settings ===")
    rows3 = await pool.fetch("""
        SELECT key, value FROM app_settings
        WHERE key LIKE '%run_taps%' OR key LIKE '%taps%'
        ORDER BY key
    """)
    for r in rows3:
        print(f"  {r['key']} = {r['value']!r}")

    await pool.close()

asyncio.run(main())
