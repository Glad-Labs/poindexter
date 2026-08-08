"""Unit tests for services.publish_service.unapprove_task — undoes an
operator approval that hasn't been published yet.

Reverses ``approve_task``'s stage_only side effect: flips
``pipeline_tasks.status`` from ``approved`` back to ``awaiting_approval``
(or ``rejected_retry`` / ``rejected_final``) and deletes any staged
``posts`` row the approve step created (never published, so nothing
external to retire — the inverse of ``publish_post_from_task``'s
``stage_only=True`` branch).
"""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest


def _make_pool(*, update_result="UPDATE 1", delete_result="DELETE 1"):
    """Build a pool whose conn yields ``update_result``/``delete_result`` for
    the transaction's two statements; ``pool.execute`` (used directly, same
    idiom as ``reject_task``) serves the best-effort gate_history insert.
    """
    conn = AsyncMock()
    conn.execute = AsyncMock(side_effect=[update_result, delete_result])
    txn_cm = MagicMock()
    txn_cm.__aenter__ = AsyncMock(return_value=conn)
    txn_cm.__aexit__ = AsyncMock(return_value=False)
    conn.transaction = MagicMock(return_value=txn_cm)
    ctx = AsyncMock()
    ctx.__aenter__ = AsyncMock(return_value=conn)
    ctx.__aexit__ = AsyncMock(return_value=False)
    pool = MagicMock()
    pool.acquire = MagicMock(return_value=ctx)
    pool.execute = AsyncMock(return_value="INSERT 0 1")
    return pool, conn


@pytest.mark.unit
class TestUnapproveTask:
    @pytest.mark.asyncio
    async def test_reverts_to_awaiting_approval_and_removes_staged_post(self):
        from services.publish_service import unapprove_task

        pool, conn = _make_pool(update_result="UPDATE 1", delete_result="DELETE 1")
        with patch("services.pipeline_db.PipelineDB") as mock_pdb_cls, patch(
            "services.router_outcome_feedback.record_task_outcome", new=AsyncMock()
        ) as mock_rfb:
            mock_pdb_cls.return_value.clear_qa_approved_snapshot = AsyncMock()
            result = await unapprove_task(pool, "task-1")

        assert result == {
            "ok": True,
            "new_status": "awaiting_approval",
            "posts_row_removed": True,
            "reason": None,
        }
        # UPDATE pipeline_tasks + DELETE posts, both inside the transaction.
        assert conn.execute.await_count == 2
        update_args = conn.execute.await_args_list[0].args
        assert update_args[1] == "task-1"
        assert update_args[2] == "awaiting_approval"
        delete_args = conn.execute.await_args_list[1].args
        assert delete_args[1] == "task-1"
        # gate_history insert runs via pool.execute (reject_task's own idiom).
        pool.execute.assert_awaited_once()
        mock_pdb_cls.return_value.clear_qa_approved_snapshot.assert_awaited_once_with("task-1")
        mock_rfb.assert_awaited_once()
        assert mock_rfb.await_args.kwargs["decision"] == "rejected"
        assert mock_rfb.await_args.kwargs["task_id"] == "task-1"

    @pytest.mark.asyncio
    async def test_reverts_to_rejected_final_with_no_staged_post(self):
        from services.publish_service import unapprove_task

        pool, conn = _make_pool(update_result="UPDATE 1", delete_result="DELETE 0")
        with patch("services.pipeline_db.PipelineDB") as mock_pdb_cls, patch(
            "services.router_outcome_feedback.record_task_outcome", new=AsyncMock()
        ):
            mock_pdb_cls.return_value.clear_qa_approved_snapshot = AsyncMock()
            result = await unapprove_task(
                pool, "task-1", target_status="rejected_final", feedback="Off-topic",
            )

        assert result["ok"] is True
        assert result["new_status"] == "rejected_final"
        assert result["posts_row_removed"] is False

    @pytest.mark.asyncio
    async def test_reverts_to_rejected_retry(self):
        from services.publish_service import unapprove_task

        pool, conn = _make_pool(update_result="UPDATE 1", delete_result="DELETE 1")
        with patch("services.pipeline_db.PipelineDB") as mock_pdb_cls, patch(
            "services.router_outcome_feedback.record_task_outcome", new=AsyncMock()
        ):
            mock_pdb_cls.return_value.clear_qa_approved_snapshot = AsyncMock()
            result = await unapprove_task(
                pool, "task-1", target_status="rejected_retry", feedback="Needs a rewrite",
            )

        assert result["new_status"] == "rejected_retry"

    @pytest.mark.asyncio
    async def test_not_currently_approved_is_idempotent_noop(self):
        from services.publish_service import unapprove_task

        pool, conn = _make_pool(update_result="UPDATE 0")
        result = await unapprove_task(pool, "task-1")

        assert result == {
            "ok": False,
            "new_status": "awaiting_approval",
            "posts_row_removed": False,
            "reason": "not_approved",
        }
        # Never reached the posts DELETE or any audit write.
        assert conn.execute.await_count == 1
        pool.execute.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_audit_trail_failures_do_not_mask_successful_revert(self):
        """Best-effort writes must never raise past unapprove_task — the
        status flip already committed and must be reported as such."""
        from services.publish_service import unapprove_task

        pool, conn = _make_pool(update_result="UPDATE 1", delete_result="DELETE 1")
        pool.execute = AsyncMock(side_effect=RuntimeError("gate_history boom"))
        with patch(
            "services.pipeline_db.PipelineDB", side_effect=RuntimeError("snapshot boom"),
        ), patch(
            "services.router_outcome_feedback.record_task_outcome",
            new=AsyncMock(side_effect=RuntimeError("outcome boom")),
        ):
            result = await unapprove_task(pool, "task-1")

        assert result["ok"] is True
        assert result["new_status"] == "awaiting_approval"


@pytest.mark.unit
class TestUnapproveTaskScheduledPost:
    """Unapprove must retract a SCHEDULED staged post, not just an approved one.

    An approve carrying ``publish_at`` promotes the staged row straight to
    ``posts.status='scheduled'``. If the delete only matched 'approved',
    unapproving would flip the task back to awaiting_approval while leaving
    the post sitting in the publish queue — and ``scheduled_publisher``
    would ship it on schedule, unapproved. The delete predicate is the only
    thing standing between those two states.
    """

    @pytest.mark.asyncio
    async def test_delete_predicate_covers_scheduled_rows(self):
        from services.publish_service import unapprove_task

        pool, conn = _make_pool(update_result="UPDATE 1", delete_result="DELETE 1")
        with patch("services.pipeline_db.PipelineDB") as mock_pdb_cls, patch(
            "services.router_outcome_feedback.record_task_outcome", new=AsyncMock()
        ):
            mock_pdb_cls.return_value.clear_qa_approved_snapshot = AsyncMock()
            result = await unapprove_task(pool, "task-1")

        assert result["ok"] is True
        assert result["posts_row_removed"] is True
        delete_sql = conn.execute.await_args_list[1].args[0]
        assert "DELETE FROM posts" in delete_sql
        # Both staged states must be in scope. A scheduled row left behind
        # publishes itself later — the failure mode this pins.
        assert "'scheduled'" in delete_sql, (
            "unapprove must delete a scheduled staged post, or an unapproved "
            "post stays in the publish queue and goes live on schedule"
        )
        assert "'approved'" in delete_sql

    @pytest.mark.asyncio
    async def test_no_longer_writes_the_dropped_scheduled_at_column(self):
        """``pipeline_tasks.scheduled_at`` was dropped as an orphan — the
        UPDATE must not reference it or every unapprove errors post-migration."""
        from services.publish_service import unapprove_task

        pool, conn = _make_pool(update_result="UPDATE 1", delete_result="DELETE 0")
        with patch("services.pipeline_db.PipelineDB") as mock_pdb_cls, patch(
            "services.router_outcome_feedback.record_task_outcome", new=AsyncMock()
        ):
            mock_pdb_cls.return_value.clear_qa_approved_snapshot = AsyncMock()
            await unapprove_task(pool, "task-1")

        update_sql = conn.execute.await_args_list[0].args[0]
        assert "scheduled_at" not in update_sql
