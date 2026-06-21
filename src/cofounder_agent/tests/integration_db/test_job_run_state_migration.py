"""integration_db: job_run_state exists after migrate, and the scheduler
run/status state has been relocated out of app_settings.

The harness (schema_loaded -> fixtures_loaded -> test_pool) runs the full
migration chain (baseline + every timestamped migration). After the chain the
job_run_state table must exist and NO plugin_job_last_run_/plugin_job_last_status_
rows may remain in app_settings. A second test re-runs the migration's up()
against hand-inserted legacy rows to pin the backfill mapping (epoch->timestamptz,
'0'->NULL, status preserved) and the delete.
"""
from __future__ import annotations

import importlib.util
import time
from pathlib import Path

import pytest

pytestmark = [
    pytest.mark.integration_db,
    pytest.mark.asyncio(loop_scope="session"),
]


def _load_migration():
    # parents: [0]=integration_db [1]=tests [2]=cofounder_agent
    mig_dir = Path(__file__).resolve().parents[2] / "services" / "migrations"
    matches = sorted(
        mig_dir.glob("*_create_job_run_state_table_and_relocate_scheduler_run_state.py")
    )
    assert matches, "create_job_run_state migration file not found"
    path = matches[-1]
    spec = importlib.util.spec_from_file_location(path.stem, path)
    assert spec is not None and spec.loader is not None
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


async def test_table_exists_and_state_relocated(test_pool):
    async with test_pool.acquire() as conn:
        cols = await conn.fetch(
            "SELECT column_name, data_type FROM information_schema.columns "
            "WHERE table_name = 'job_run_state' ORDER BY ordinal_position"
        )
        leftover = await conn.fetchval(
            "SELECT count(*) FROM app_settings "
            "WHERE key LIKE 'plugin_job_last_run_%' OR key LIKE 'plugin_job_last_status_%'"
        )
    by_name = {c["column_name"]: c["data_type"] for c in cols}
    assert by_name, "job_run_state table missing after migration chain"
    assert by_name["job_name"] == "text"
    assert by_name["last_run_at"] == "timestamp with time zone"
    assert by_name["last_status"] == "text"
    assert by_name["updated_at"] == "timestamp with time zone"
    assert leftover == 0, "scheduler state rows still in app_settings after migrate"


async def test_backfill_maps_epoch_status_and_deletes(test_pool):
    mod = _load_migration()
    real = int(time.time()) - 3600
    async with test_pool.acquire() as conn:
        # Clean any residue from a prior run of this test.
        await conn.execute(
            "DELETE FROM job_run_state WHERE job_name IN ('alpha__bf_test', 'beta__bf_test')"
        )
        await conn.execute(
            "DELETE FROM app_settings WHERE key LIKE 'plugin_job_last_%__bf_test'"
        )
        for key, val in [
            ("plugin_job_last_run_alpha__bf_test", str(real)),
            ("plugin_job_last_status_alpha__bf_test", "ok"),
            ("plugin_job_last_run_beta__bf_test", "0"),
            ("plugin_job_last_status_beta__bf_test", "err"),
        ]:
            await conn.execute(
                "INSERT INTO app_settings (key, value, category, is_active) "
                "VALUES ($1, $2, 'plugin_telemetry', true) "
                "ON CONFLICT (key) DO UPDATE SET value = EXCLUDED.value",
                key, val,
            )

    # Re-run the migration; it is idempotent (CREATE IF NOT EXISTS, backfill
    # picks up the new rows, delete removes them).
    await mod.up(test_pool)

    async with test_pool.acquire() as conn:
        a = await conn.fetchrow(
            "SELECT last_run_at, last_status FROM job_run_state WHERE job_name = $1",
            "alpha__bf_test",
        )
        b = await conn.fetchrow(
            "SELECT last_run_at, last_status FROM job_run_state WHERE job_name = $1",
            "beta__bf_test",
        )
        leftover = await conn.fetchval(
            "SELECT count(*) FROM app_settings WHERE key LIKE 'plugin_job_last_%__bf_test'"
        )

    assert a is not None and a["last_run_at"] is not None
    assert abs(a["last_run_at"].timestamp() - real) < 2
    assert a["last_status"] == "ok"
    assert b is not None
    assert b["last_run_at"] is None  # '0' sentinel maps to NULL, not 1970
    assert b["last_status"] == "err"
    assert leftover == 0
