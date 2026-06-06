import asyncio

import asyncpg

DB_URL = "postgresql://poindexter:poindexter-brain-local@localhost:15432/poindexter_brain"

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
