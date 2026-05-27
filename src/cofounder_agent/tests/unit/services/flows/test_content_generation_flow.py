"""Tests for ``services.flows.content_generation`` (Glad-Labs/poindexter#410).

Cover the three contracts the Phase-0 cutover seam relies on:

1. ``claim_pending_task`` returns None when the queue is empty + claims
   one row when something's pending. Sticky semantics (``FOR UPDATE
   SKIP LOCKED``) so concurrent flow runs never grab the same task.
2. ``content_generation_flow`` calls ``process_content_generation_task``
   with the claimed row's parameters when invoked schedule-driven.
3. The same flow respects operator-supplied parameters when invoked
   on-demand (CLI / FastAPI route) — same shape as today's call sites.

The flow itself is thin (Lane C already moved orchestration to LangGraph);
tests focus on the dispatch surface, not the pipeline body.
"""

from __future__ import annotations

from contextlib import asynccontextmanager
from unittest.mock import AsyncMock, MagicMock, patch

import pytest


def _make_pool(claim_row=None):
    """asyncpg pool double whose claim transaction returns ``claim_row``."""
    conn = MagicMock()
    conn.fetchrow = AsyncMock(return_value=claim_row)
    conn.execute = AsyncMock()

    @asynccontextmanager
    async def _tx():
        yield

    conn.transaction = MagicMock(side_effect=lambda *a, **kw: _tx())

    pool = MagicMock()

    @asynccontextmanager
    async def _acquire():
        yield conn

    pool.acquire = _acquire
    return pool


def _make_db_service(pool):
    db = MagicMock()
    db.pool = pool
    return db


@pytest.mark.unit
class TestClaimPendingTask:
    @pytest.mark.asyncio
    async def test_returns_none_when_queue_empty(self):
        from services.flows.content_generation import claim_pending_task

        pool = _make_pool(claim_row=None)
        db = _make_db_service(pool)
        result = await claim_pending_task.fn(db)
        assert result is None

    @pytest.mark.asyncio
    async def test_returns_claimed_row(self):
        from services.flows.content_generation import claim_pending_task

        row = {
            "task_id": "abc-123",
            "topic": "Why local Ollama beats cloud LLMs",
            "style": "technical",
            "tone": "candid",
            "target_length": 1200,
            "category": "ai_ml",
            "target_audience": "indie devs",
            "niche_slug": "ai_ml",
            "template_slug": "canonical_blog",
            "primary_keyword": "ollama",
            "site_id": None,
        }
        pool = _make_pool(claim_row=row)
        db = _make_db_service(pool)
        result = await claim_pending_task.fn(db)
        assert result is not None
        assert result["task_id"] == "abc-123"
        assert result["template_slug"] == "canonical_blog"

    @pytest.mark.asyncio
    async def test_returns_none_when_pool_missing(self):
        """Defensive: ``database_service.pool=None`` means the worker is
        in a broken bootstrap state. The flow shouldn't crash there."""
        from services.flows.content_generation import claim_pending_task

        db = MagicMock()
        db.pool = None
        result = await claim_pending_task.fn(db)
        assert result is None

    @pytest.mark.asyncio
    async def test_atomically_transitions_to_in_progress(self):
        """``UPDATE pipeline_tasks SET status='in_progress'`` runs inside
        the same transaction as the SELECT FOR UPDATE — concurrent flow
        runs see the row as locked and skip it."""
        from services.flows.content_generation import claim_pending_task

        row = {
            "task_id": "xyz-789",
            "topic": "x",
            "style": "x",
            "tone": "x",
            "target_length": 1500,
            "category": None,
            "target_audience": None,
            "niche_slug": None,
            "template_slug": None,
            "primary_keyword": None,
            "site_id": None,
        }
        pool = _make_pool(claim_row=row)
        db = _make_db_service(pool)
        await claim_pending_task.fn(db)
        # The conn double recorded an execute call for the UPDATE
        async with pool.acquire() as conn:
            conn.execute.assert_called_once()
            sql_arg = conn.execute.call_args.args[0]
            assert "UPDATE pipeline_tasks" in sql_arg
            assert "in_progress" in sql_arg


@pytest.mark.unit
class TestContentGenerationFlow:
    """End-to-end: the flow's dispatch shape — does it route to
    ``process_content_generation_task`` with the right args?"""

    @pytest.mark.asyncio
    async def test_schedule_driven_empty_queue_exits_clean(self):
        """No args + empty queue → ``{"claimed": False}`` without
        invoking the pipeline."""
        from services.flows.content_generation import content_generation_flow

        pool = _make_pool(claim_row=None)
        db = _make_db_service(pool)

        with patch(
            "services.content_router_service.process_content_generation_task",
        ) as pipeline_mock:
            result = await content_generation_flow.fn(database_service=db)

        assert result == {"claimed": False, "task_id": None}
        pipeline_mock.assert_not_called()

    @pytest.mark.asyncio
    async def test_schedule_driven_claimed_row_runs_pipeline(self):
        """Claim a row → call pipeline with the row's parameters."""
        from services.flows.content_generation import content_generation_flow

        row = {
            "task_id": "task-claim-1",
            "topic": "Why FastAPI beats Flask for AI APIs",
            "style": "technical",
            "tone": "candid",
            "target_length": 1500,
            "category": "ai_ml",
            "target_audience": "indie devs",
            "niche_slug": "ai_ml",
            "template_slug": "canonical_blog",
            "primary_keyword": "fastapi",
            "site_id": None,
        }
        pool = _make_pool(claim_row=row)
        db = _make_db_service(pool)

        pipeline_mock = AsyncMock(return_value={"status": "awaiting_approval"})
        with patch(
            "services.content_router_service.process_content_generation_task",
            new=pipeline_mock,
        ):
            result = await content_generation_flow.fn(database_service=db)

        assert result["claimed"] is True
        assert result["task_id"] == "task-claim-1"
        pipeline_mock.assert_called_once()
        kwargs = pipeline_mock.call_args.kwargs
        assert kwargs["topic"] == "Why FastAPI beats Flask for AI APIs"
        assert kwargs["task_id"] == "task-claim-1"
        assert kwargs["category"] == "ai_ml"

    @pytest.mark.asyncio
    async def test_operator_triggered_uses_supplied_args(self):
        """Caller passes ``task_id`` + ``topic`` directly — flow doesn't
        try to claim from queue, just runs the pipeline. Parity with
        existing CLI / REST entry points."""
        from services.flows.content_generation import content_generation_flow

        pool = _make_pool(claim_row=None)  # queue is empty but irrelevant
        db = _make_db_service(pool)
        pipeline_mock = AsyncMock(return_value={"status": "awaiting_approval"})

        with patch(
            "services.content_router_service.process_content_generation_task",
            new=pipeline_mock,
        ):
            result = await content_generation_flow.fn(
                task_id="manual-task",
                topic="Manual topic",
                style="conversational",
                tone="friendly",
                target_length=900,
                database_service=db,
            )

        assert result["claimed"] is True
        assert result["task_id"] == "manual-task"
        pipeline_mock.assert_called_once()
        kwargs = pipeline_mock.call_args.kwargs
        assert kwargs["topic"] == "Manual topic"
        assert kwargs["style"] == "conversational"

    @pytest.mark.asyncio
    async def test_topic_required_when_no_claim_no_caller_arg(self):
        """If queue is empty AND no topic supplied, exit cleanly —
        don't crash the schedule with a ``required arg missing``
        error. The schedule-driven case ALWAYS hits this branch when
        the queue is empty; the operator-triggered case is the only
        path that should error if topic is missing AND the operator
        explicitly passed task_id without topic."""
        from services.flows.content_generation import content_generation_flow

        pool = _make_pool(claim_row=None)
        db = _make_db_service(pool)

        # task_id explicitly supplied but topic missing → real error
        with pytest.raises(ValueError, match="requires a topic"):
            await content_generation_flow.fn(
                task_id="no-topic-task",
                database_service=db,
            )


# ---------------------------------------------------------------------------
# Cycle-5 #253: flow-crash recovery
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestFlowCrashMarksTaskFailed:
    """Pre-#253, an unhandled exception in ``process_content_generation_task``
    propagated to Prefect and left the claimed task ``status='in_progress'``
    forever (the sweep_stale_tasks safety net was also broken, so the row
    needed manual intervention). The fix wraps the pipeline call in a
    try/except that marks the task failed before re-raising, so:

    * Prefect still sees the failure (re-raise preserves UI / metrics)
    * The DB row reflects the real terminal state immediately
    * The brain daemon + approval queue + cost dashboards don't show
      a phantom in-progress task
    """

    @pytest.mark.asyncio
    async def test_pipeline_crash_marks_task_failed_then_reraises(self):
        from services.flows.content_generation import content_generation_flow

        pool = _make_pool(claim_row=None)
        db = _make_db_service(pool)

        boom = RuntimeError("LLM provider returned 500")
        pipeline_mock = AsyncMock(side_effect=boom)

        with patch(
            "services.content_router_service.process_content_generation_task",
            new=pipeline_mock,
        ):
            with pytest.raises(RuntimeError, match="LLM provider returned 500"):
                await content_generation_flow.fn(
                    task_id="crash-task-1",
                    topic="A topic that crashes the pipeline",
                    database_service=db,
                )

        # The mark-failed helper opens an extra acquire() and runs an
        # UPDATE on pipeline_tasks. We can check the conn from the same
        # _make_pool double — its execute() was called with the failed
        # UPDATE statement.
        async with pool.acquire() as conn:
            # Find any execute call whose SQL matches the failed UPDATE
            update_calls = [
                c for c in conn.execute.call_args_list
                if "SET status = 'failed'" in c.args[0]
            ]
            assert update_calls, "expected an UPDATE ... SET status='failed' call"
            sql, error_message, task_id = update_calls[0].args
            assert "UPDATE pipeline_tasks" in sql
            assert "WHERE task_id = $2 AND status = 'in_progress'" in sql
            assert "LLM provider returned 500" in error_message
            assert error_message.startswith("flow crashed: RuntimeError:")
            assert task_id == "crash-task-1"

    @pytest.mark.asyncio
    async def test_helper_truncates_error_message(self):
        """The error_message column is bounded; the helper truncates to
        2KB so an attacker-controlled exception text can't bloat the DB."""
        from services.flows.content_generation import _mark_task_failed_on_flow_crash

        pool = _make_pool(claim_row=None)
        db = _make_db_service(pool)

        huge_error = RuntimeError("A" * 10_000)
        await _mark_task_failed_on_flow_crash(
            database_service=db,
            task_id="t-truncate",
            error=huge_error,
        )

        async with pool.acquire() as conn:
            update_calls = [
                c for c in conn.execute.call_args_list
                if "SET status = 'failed'" in c.args[0]
            ]
            assert update_calls, "expected an UPDATE ... SET status='failed' call"
            _, error_message, _ = update_calls[0].args
            assert len(error_message) == 2048

    @pytest.mark.asyncio
    async def test_helper_noop_when_task_id_none(self):
        """Schedule-driven retries with an exhausted queue have
        task_id=None at the call site — helper must be a no-op rather
        than crashing or running a NULL UPDATE."""
        from services.flows.content_generation import _mark_task_failed_on_flow_crash

        pool = _make_pool(claim_row=None)
        db = _make_db_service(pool)

        await _mark_task_failed_on_flow_crash(
            database_service=db,
            task_id=None,
            error=RuntimeError("anything"),
        )

        async with pool.acquire() as conn:
            update_calls = [
                c for c in conn.execute.call_args_list
                if "SET status = 'failed'" in c.args[0]
            ]
            assert not update_calls, (
                "helper must not run an UPDATE when task_id is None"
            )

    @pytest.mark.asyncio
    async def test_helper_swallows_db_errors(self):
        """The helper is the eager-cleanup path; the sweep is the
        safety net. If the helper's UPDATE itself fails (network blip,
        pool exhausted, etc.) it must NOT mask the original pipeline
        exception — log + return so the caller's re-raise propagates."""
        from services.flows.content_generation import _mark_task_failed_on_flow_crash

        # Build a DB service whose pool acquire raises
        db = MagicMock()
        pool = MagicMock()

        @asynccontextmanager
        async def _acquire():
            raise RuntimeError("conn lost mid-cleanup")
            yield  # unreachable but satisfies typing
        pool.acquire = _acquire
        db.pool = pool

        # Must not raise — best-effort path
        await _mark_task_failed_on_flow_crash(
            database_service=db,
            task_id="t-db-error",
            error=RuntimeError("the original crash"),
        )

    @pytest.mark.asyncio
    async def test_helper_targets_only_in_progress_rows(self):
        """The WHERE clause must include ``AND status = 'in_progress'`` —
        regression guard against accidentally re-failing a row that
        already transitioned to ``awaiting_approval`` / ``published``
        between the pipeline call and the cleanup."""
        from services.flows.content_generation import _mark_task_failed_on_flow_crash

        pool = _make_pool(claim_row=None)
        db = _make_db_service(pool)

        await _mark_task_failed_on_flow_crash(
            database_service=db,
            task_id="t-guard",
            error=RuntimeError("test"),
        )

        async with pool.acquire() as conn:
            update_calls = [
                c for c in conn.execute.call_args_list
                if "SET status = 'failed'" in c.args[0]
            ]
            assert update_calls
            sql = update_calls[0].args[0]
            assert "WHERE task_id = $2 AND status = 'in_progress'" in sql
