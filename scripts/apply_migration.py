"""Apply a single migration file to prod DB."""
import asyncio
import importlib.util
import sys

import asyncpg

DB_URL = "postgresql://poindexter:poindexter-brain-local@localhost:5433/poindexter_brain"
MIGRATION_PATH = sys.argv[1]


async def main():
    pool = await asyncpg.create_pool(DB_URL)
    spec = importlib.util.spec_from_file_location("migration", MIGRATION_PATH)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    await mod.up(pool)
    name = MIGRATION_PATH.split("\\")[-1]
    await pool.execute(
        "INSERT INTO schema_migrations (name) VALUES ($1) ON CONFLICT DO NOTHING",
        name,
    )
    print(f"Applied: {name}")
    await pool.close()

asyncio.run(main())
