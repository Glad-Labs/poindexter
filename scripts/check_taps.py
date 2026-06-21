import asyncio

import asyncpg

DB_URL = "postgresql://poindexter:poindexter-brain-local@localhost:5433/poindexter_brain"

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
