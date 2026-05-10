"""``claim_pending_task`` against the real ``pipeline_tasks`` schema (#410).

Phase 0 shipped a SELECT that referenced a non-existent ``stage_data``
column; the 8 mocked unit tests passed because they validated the
dict-consumer shape only, never the SQL itself. Stage 2 canary
exposed it the moment the flag flipped.

This test runs the actual claim helper inside the existing
``integration_db`` tier — every migration in
``services/migrations/`` is replayed against a disposable
``poindexter_test_<hex>`` DB at session start, so the column list in
the SELECT is checked against the real schema. ``test_txn`` rolls
back automatically, so the test never leaks rows.

Run with::

    poetry run pytest tests/integration_db/test_claim_pending_task.py \
        -m integration_db -q

(or any of the broader ``-m integration_db`` invocations).
"""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest

pytestmark = [
    pytest.mark.integration_db,
    # Session-scoped fixtures need the session loop, matching the
    # pattern used by ``test_harness_smoke.py`` next door.
    pytest.mark.asyncio(loop_scope="session"),
]


def _db_double(pool):
    """``claim_pending_task`` only touches ``database_service.pool``,
    so a MagicMock with that attribute is enough — no need to spin
    up a full DatabaseService for a SQL-only test."""
    db = MagicMock()
    db.pool = pool
    return db


async def test_claim_returns_pending_row_and_flips_status(test_pool) -> None:
    """Insert one pending row, claim it, verify status flips to in_progress.

    The claim helper opens its own connection from the pool, so the
    inserted row must be COMMITTED for the claim to see it. We can't
    use ``test_txn``'s auto-rollback here — the claim would never see
    a row inserted inside an uncommitted transaction. The row is
    removed in the ``finally`` block so the session DB stays clean.
    """
    from services.flows.content_generation import claim_pending_task

    task_id = "claim-real-schema-smoke"
    async with test_pool.acquire() as setup:
        # Defensive cleanup in case a prior failed run left the row.
        await setup.execute(
            "DELETE FROM pipeline_tasks WHERE task_id = $1", task_id
        )
        await setup.execute(
            """
            INSERT INTO pipeline_tasks
                (task_id, task_type, topic, status, stage, style, tone,
                 target_length)
            VALUES ($1, 'blog_post', $2, 'pending', 'pending',
                    'technical', 'professional', 900)
            """,
            task_id,
            "integration_db smoke: claim works against real schema",
        )

    try:
        db = _db_double(test_pool)
        claimed = await claim_pending_task.fn(db)
        assert claimed is not None, (
            "claim_pending_task returned None despite a pending row "
            "— the SELECT may not match the real pipeline_tasks schema"
        )
        assert claimed["task_id"] == task_id
        assert claimed["topic"].startswith("integration_db smoke")
        assert claimed["target_length"] == 900
        # The SELECT also reads columns the SQL drift bug would have
        # broken — list them explicitly so a future deletion is caught.
        for required_col in (
            "task_id", "topic", "style", "tone", "target_length",
            "category", "target_audience", "niche_slug",
            "template_slug", "primary_keyword", "site_id",
        ):
            assert required_col in claimed, (
                f"claim_pending_task row missing column {required_col!r} "
                f"— SELECT got out of sync with the consumer"
            )

        async with test_pool.acquire() as verify:
            status = await verify.fetchval(
                "SELECT status FROM pipeline_tasks WHERE task_id = $1",
                task_id,
            )
        assert status == "in_progress", (
            f"expected 'in_progress' after claim, got {status!r}"
        )
    finally:
        async with test_pool.acquire() as cleanup:
            await cleanup.execute(
                "DELETE FROM pipeline_tasks WHERE task_id = $1", task_id
            )


async def test_claim_returns_none_when_queue_empty(test_pool) -> None:
    """When ``pipeline_tasks`` has no ``status='pending'`` row, the
    claim helper must return None instead of crashing. We can't rely
    on the test DB being empty (other tests may leave rows mid-run if
    cleanup races), so we lock everything pending FOR UPDATE on a
    separate connection to simulate an empty queue."""
    from services.flows.content_generation import claim_pending_task

    async with test_pool.acquire() as locker:
        txn = locker.transaction()
        await txn.start()
        try:
            await locker.execute(
                "SELECT 1 FROM pipeline_tasks WHERE status = 'pending' "
                "FOR UPDATE"
            )
            db = _db_double(test_pool)
            claimed = await claim_pending_task.fn(db)
            assert claimed is None
        finally:
            await txn.rollback()
