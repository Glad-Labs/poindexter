"""live_activity begin/update/finish roundtrip against the real table."""
import pytest

from services import live_activity

pytestmark = [
    pytest.mark.integration_db,
    pytest.mark.asyncio(loop_scope="session"),
]


async def test_begin_update_finish_roundtrip(test_pool):
    aid = await live_activity.begin(
        test_pool, kind="job", ref_id="demo_job", title="Demo job"
    )
    assert isinstance(aid, int)
    async with test_pool.acquire() as c:
        row = await c.fetchrow(
            "SELECT status, finished_at FROM live_activity WHERE id=$1", aid
        )
    assert row["status"] == "running" and row["finished_at"] is None

    await live_activity.update(test_pool, aid, step="qa.critic", pct=62)
    await live_activity.finish(test_pool, aid, status="ok")
    async with test_pool.acquire() as c:
        row = await c.fetchrow(
            "SELECT step, progress_pct, status, finished_at "
            "FROM live_activity WHERE id=$1",
            aid,
        )
    assert row["step"] == "qa.critic" and row["progress_pct"] == 62
    assert row["status"] == "ok" and row["finished_at"] is not None


async def test_update_finish_noop_on_none_id(test_pool):
    # A failed begin() returns None; update/finish must silently no-op.
    await live_activity.update(test_pool, None, step="x")
    await live_activity.finish(test_pool, None)
