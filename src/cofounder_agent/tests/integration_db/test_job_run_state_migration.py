"""integration_db: job_run_state exists after migrate, and the scheduler
run/status state has been relocated out of app_settings.

The harness (schema_loaded -> fixtures_loaded -> test_pool) runs the full
migration chain (the squashed baseline). After the chain the job_run_state
table must exist and NO plugin_job_last_run_/plugin_job_last_status_ rows may
remain in app_settings.

(A second test used to re-run the create_job_run_state migration's up() against
hand-inserted legacy rows to pin the epoch->timestamptz / '0'->NULL backfill
mapping. That migration was folded into the Phase F squash baseline and its file
deleted, so the module-loading test was removed with it — the table + relocation
invariant below is asserted directly against the baseline-applied schema.)
"""
from __future__ import annotations

import pytest

pytestmark = [
    pytest.mark.integration_db,
    pytest.mark.asyncio(loop_scope="session"),
]


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
