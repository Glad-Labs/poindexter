"""live_activity table exists with the expected columns + indexes after migrations."""
import pytest

pytestmark = [
    pytest.mark.integration_db,
    pytest.mark.asyncio(loop_scope="session"),
]


async def test_live_activity_columns(test_pool):  # test_pool: session pool with migrations applied
    async with test_pool.acquire() as conn:
        cols = {
            r["column_name"]: r["data_type"]
            for r in await conn.fetch(
                "SELECT column_name, data_type FROM information_schema.columns "
                "WHERE table_name = 'live_activity'"
            )
        }
    assert cols["kind"] == "text"
    assert cols["progress_pct"] == "smallint"
    assert cols["finished_at"] == "timestamp with time zone"
    assert "detail" in cols  # jsonb


async def test_live_activity_running_index(test_pool):
    async with test_pool.acquire() as conn:
        idx = [
            r["indexname"]
            for r in await conn.fetch(
                "SELECT indexname FROM pg_indexes WHERE tablename = 'live_activity'"
            )
        ]
    assert any("running" in n for n in idx)
