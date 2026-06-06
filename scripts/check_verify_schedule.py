import asyncio

import asyncpg

DB_URL = "postgresql://poindexter:poindexter-brain-local@localhost:15432/poindexter_brain"

async def main():
    pool = await asyncpg.create_pool(DB_URL)
    print("=== verify-related settings ===")
    rows = await pool.fetch(
        "SELECT key, value FROM app_settings WHERE key LIKE $1 ORDER BY key",
        "%verify%",
    )
    for r in rows:
        print(f"  {r['key']} = {r['value']!r}")

    print("\n=== job-schedule settings ===")
    rows2 = await pool.fetch(
        "SELECT key, value FROM app_settings WHERE key LIKE $1 ORDER BY key",
        "%plugin.job%",
    )
    for r in rows2:
        print(f"  {r['key']} = {r['value']!r}")

    await pool.close()

asyncio.run(main())
