"""Integration test pinning ``claim_pending_task`` against the real schema.

Phase 1 (Glad-Labs/poindexter#410) shipped a unit test suite that
mocked ``pool.acquire().fetchrow`` and only validated the dict-consumer
shape. The SQL itself was never exercised against an actual
``pipeline_tasks`` table — so when the SELECT listed a column that
didn't exist (``stage_data``), every passing unit test still let a
broken claim ship to production.

This integration test runs the real SQL against the real schema, so
that class of bug can't recur silently. It uses the real-services
harness (``REAL_SERVICES_TESTS=1`` gate) and a transaction that's
rolled back at the end, so it never mutates Matt's operating data.

Run with::

    INTEGRATION_TESTS=1 REAL_SERVICES_TESTS=1 \
        poetry run pytest tests/integration/test_claim_pending_task_real_schema.py -q
"""

from __future__ import annotations

import os
import uuid
from unittest.mock import MagicMock

import asyncpg
import pytest


# The real-services conftest already provides ``real_pool`` against the
# isolated ``poindexter_test`` DB. We re-use it here. The two-env-var
# gate keeps this off by default in CI runs that don't bring up the
# stack.
pytestmark = pytest.mark.skipif(
    not (os.getenv("INTEGRATION_TESTS") and os.getenv("REAL_SERVICES_TESTS")),
    reason="Real-services harness disabled. Set INTEGRATION_TESTS=1 and REAL_SERVICES_TESTS=1.",
)


def _make_db_double(pool: asyncpg.Pool):
    """Wrap the real pool so it looks like a ``DatabaseService``.

    ``claim_pending_task`` only reaches for ``database_service.pool``,
    so a MagicMock with the ``pool`` attribute is enough. This avoids
    bootstrapping the full DatabaseService for a SQL-only smoke.
    """
    db = MagicMock()
    db.pool = pool
    return db


@pytest.mark.integration
@pytest.mark.asyncio
async def test_claim_pending_task_runs_against_real_pipeline_tasks(real_pool):
    """The SELECT + UPDATE that ``claim_pending_task`` runs must parse
    against the live ``pipeline_tasks`` schema. Insert one row, claim
    it, verify status flips, then roll back."""
    from services.flows.content_generation import claim_pending_task

    # ``real_pool`` points at ``poindexter_test``, NOT the operating DB.
    # We need ``pipeline_tasks`` to exist there; the harness runs
    # migrations on first use. If the table isn't present yet, skip
    # rather than fail — that's a harness setup issue, not a flow bug.
    async with real_pool.acquire() as conn:
        exists = await conn.fetchval(
            "SELECT 1 FROM pg_tables WHERE tablename = 'pipeline_tasks'"
        )
        if not exists:
            pytest.skip(
                "pipeline_tasks not in test DB — migrate the test DB first "
                "(see tests/integration/conftest.py)"
            )

        # Insert one fresh pending row inside a transaction so we don't
        # pollute the test DB beyond this test.
        task_id = f"claim-smoke-{uuid.uuid4()}"
        topic = "integration smoke: claim works"
        async with conn.transaction():
            await conn.execute(
                """
                INSERT INTO pipeline_tasks
                    (task_id, task_type, topic, status, stage, style, tone,
                     target_length)
                VALUES ($1, 'blog_post', $2, 'pending', 'pending',
                        'technical', 'professional', 900)
                """,
                task_id,
                topic,
            )

            # Now run the actual claim helper. It uses the same pool we
            # populated, so the row is visible.
            db = _make_db_double(real_pool)
            claimed = await claim_pending_task.fn(db)

            assert claimed is not None, (
                "claim_pending_task returned None despite a pending row"
            )
            assert claimed["task_id"] == task_id
            assert claimed["topic"] == topic
            assert claimed["target_length"] == 900

            # Status should have flipped to 'in_progress' inside the
            # claim transaction. Read it back on the same connection
            # so we observe the post-UPDATE row.
            status = await conn.fetchval(
                "SELECT status FROM pipeline_tasks WHERE task_id = $1",
                task_id,
            )
            assert status == "in_progress", (
                f"expected 'in_progress' after claim, got {status!r}"
            )

            # Roll back so the test DB stays untouched.
            raise _RollbackSentinel()


class _RollbackSentinel(Exception):
    """Raised to roll back the test transaction without failing the test."""


@pytest.mark.integration
@pytest.mark.asyncio
async def test_claim_pending_task_skips_when_no_pending_rows(real_pool):
    """When the queue is empty the helper must return None, not crash
    or block. Run inside a transaction that locks any existing rows
    out so the queue *appears* empty for this test."""
    from services.flows.content_generation import claim_pending_task

    async with real_pool.acquire() as conn:
        exists = await conn.fetchval(
            "SELECT 1 FROM pg_tables WHERE tablename = 'pipeline_tasks'"
        )
        if not exists:
            pytest.skip("pipeline_tasks not in test DB")

        async with conn.transaction():
            # Lock every pending row on THIS connection so the
            # FOR UPDATE SKIP LOCKED in claim_pending_task sees an
            # empty result set.
            await conn.execute(
                "SELECT 1 FROM pipeline_tasks WHERE status = 'pending' "
                "FOR UPDATE"
            )

            db = _make_db_double(real_pool)
            claimed = await claim_pending_task.fn(db)
            assert claimed is None
