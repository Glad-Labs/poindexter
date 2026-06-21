"""Apply a single migration file to prod DB."""
import asyncio
import importlib.util
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
MIGRATION_PATH = sys.argv[1]


async def main():
    pool = await asyncpg.create_pool(DB_URL)
    spec = importlib.util.spec_from_file_location("migration", MIGRATION_PATH)
    assert spec and spec.loader, f"cannot load migration from {MIGRATION_PATH}"
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
