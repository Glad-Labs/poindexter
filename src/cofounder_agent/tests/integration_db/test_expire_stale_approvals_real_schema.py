"""ExpireStaleApprovalsJob against the real schema (poindexter#981).

The job's only failure mode in prod was invisible to the unit tier: it
does ``UPDATE content_tasks SET status = 'expired'`` and the mocked pool
can't enforce ``pipeline_tasks_status_check``, so the suite stayed green
for months while the job crashed on every row it actually had to expire
(2026-07-23 and 2026-08-06 ``job-fail`` findings).

This tier replays every migration against a disposable DB, so the test
exercises the real constraint (as amended by the 20260816_021929
migration), the ``content_tasks`` INSTEAD OF UPDATE trigger, and the
trigger's completed_at CASE — end to end.
"""

from __future__ import annotations

import pytest

pytestmark = [
    pytest.mark.integration_db,
    pytest.mark.asyncio(loop_scope="session"),
]

_TASK_ID = "expire-stale-real-schema-smoke"


async def test_expiry_write_passes_constraint_and_stamps_completed_at(
    test_pool,
) -> None:
    """Insert a stale awaiting_approval row, run the job, verify it lands
    at 'expired' with completed_at stamped by the view trigger.

    The job acquires its own connection, so the setup row must be
    committed (no ``test_txn`` auto-rollback); cleanup happens in
    ``finally``.
    """
    from services.jobs.expire_stale_approvals import ExpireStaleApprovalsJob

    async with test_pool.acquire() as setup:
        await setup.execute(
            "DELETE FROM pipeline_tasks WHERE task_id = $1", _TASK_ID
        )
        await setup.execute(
            """
            INSERT INTO pipeline_tasks
                (task_id, task_type, topic, status, stage, style, tone,
                 target_length, updated_at)
            VALUES ($1, 'blog_post', $2, 'awaiting_approval', 'completed',
                    'technical', 'professional', 900,
                    NOW() - INTERVAL '10 days')
            """,
            _TASK_ID,
            "integration_db smoke: stale approval expires against real schema",
        )

    try:
        result = await ExpireStaleApprovalsJob().run(
            test_pool, {"ttl_days": 7}
        )
        assert result.ok, (
            f"job crashed against the real schema: {result.detail} — the "
            "status constraint likely no longer allows 'expired'"
        )
        assert result.changes_made >= 1

        async with test_pool.acquire() as conn:
            row = await conn.fetchrow(
                "SELECT status, completed_at FROM pipeline_tasks "
                "WHERE task_id = $1",
                _TASK_ID,
            )
        assert row is not None
        assert row["status"] == "expired"
        # Stamped by the content_tasks_update_redirect trigger's CASE —
        # proves the migration's trigger replacement is live, not just
        # the constraint swap.
        assert row["completed_at"] is not None
    finally:
        async with test_pool.acquire() as cleanup:
            await cleanup.execute(
                "DELETE FROM pipeline_tasks WHERE task_id = $1", _TASK_ID
            )
