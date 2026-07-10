"""get_live_activity read (freshness window + summary) and reap_stale."""
import pytest

from services import live_activity

pytestmark = [
    pytest.mark.integration_db,
    pytest.mark.asyncio(loop_scope="session"),
]


async def test_running_excludes_stale_and_finished(test_pool):
    async with test_pool.acquire() as c:
        await c.execute("DELETE FROM live_activity")
        # fresh running (shows), stale running (hidden by window), finished (in recent)
        await c.execute(
            "INSERT INTO live_activity (kind, ref_id, title) VALUES ('content','1','Fresh')"
        )
        await c.execute(
            "INSERT INTO live_activity (kind, ref_id, title, updated_at) "
            "VALUES ('job','2','Stale', now() - interval '10 minutes')"
        )
        await c.execute(
            "INSERT INTO live_activity (kind, ref_id, title, status, finished_at) "
            "VALUES ('job','3','Done','ok', now())"
        )
    out = await live_activity.get_live_activity(
        test_pool, freshness_seconds=120, recent_limit=20
    )
    running_titles = {r["title"] for r in out["running"]}
    assert running_titles == {"Fresh"}
    assert any(r["title"] == "Done" for r in out["recent"])
    assert out["summary"]["running_by_kind"].get("content") == 1


async def test_reap_marks_stale(test_pool):
    async with test_pool.acquire() as c:
        await c.execute("DELETE FROM live_activity")
        await c.execute(
            "INSERT INTO live_activity (kind, ref_id, title, updated_at) "
            "VALUES ('job','9','Orphan', now() - interval '10 minutes')"
        )
    n = await live_activity.reap_stale(test_pool, reaper_seconds=300)
    assert n == 1
    async with test_pool.acquire() as c:
        row = await c.fetchrow(
            "SELECT status, finished_at FROM live_activity WHERE ref_id='9'"
        )
    assert row["status"] == "stale" and row["finished_at"] is not None
