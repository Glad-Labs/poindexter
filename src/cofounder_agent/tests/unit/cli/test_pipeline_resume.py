"""Regression tests: ``poindexter pipeline resume`` atomicity (b + c1).

The command recorded the gate approval (clearing ``awaiting_gate`` + writing an
``approved`` pipeline_gate_history row) BEFORE re-invoking the graph, with no
rollback if the resume failed. Two failure shapes, two fixes:

* **Resume RAISES** (e.g. the Postgres checkpointer can't be set up — the
  graph never advances past the gate). → compensate: roll back the approval and
  restore the pause so the task is immediately re-resumable, and no stale
  approval lingers. (b)
* **Resume returns halted** (a downstream node failed AFTER the gate passed, or
  QA legitimately rejected). The gate WAS consumed — do NOT roll back. The task
  is stranded ``in_progress`` past its gate with an intact checkpoint; the
  *continue-resume* path lets the operator resume again from the checkpoint
  instead of being told "not paused at a gate". (c1)

Headline regression coverage (what the incident needs): a failed resume leaves
the task re-resumable AND drops no stale approval that could auto-pass a fresh
run.
"""

from __future__ import annotations

import asyncio
import json
from contextlib import ExitStack
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from click.testing import CliRunner

from poindexter.cli.pipeline import resume_command

pytestmark = pytest.mark.unit

_TID = "abc12345-0000-0000-0000-000000000000"


def _paused_row(**over):
    row = {
        "task_id": _TID,
        "status": "awaiting_gate",
        "awaiting_gate": "draft_gate",
        "gate_artifact": '{"title": "X"}',
        "gate_paused_at": None,
        "topic": "Topic",
        "template_slug": "canonical_blog",
    }
    row.update(over)
    return row


def _patches(
    *,
    row,
    approve=None,
    rollback=None,
    run_result=None,
    run_side_effect=None,
    has_ckpt=False,
    approved_gate=None,
):
    """Context-manager bundle wiring the resume_command seams to doubles."""
    runner_cls = MagicMock()
    runner_cls.return_value.run = AsyncMock(
        return_value=run_result, side_effect=run_side_effect,
    )
    patches = [
        patch("poindexter.cli.pipeline._make_pool",
              new=AsyncMock(return_value=MagicMock(close=AsyncMock()))),
        patch("poindexter.cli.pipeline._make_site_config",
              new=AsyncMock(return_value=MagicMock())),
        patch("poindexter.cli.pipeline._fetch_paused_row",
              new=AsyncMock(return_value=row)),
        patch("services.approval_service.approve",
              new=approve or AsyncMock(return_value={
                  "ok": True, "task_id": _TID,
                  "gate_name": "draft_gate", "gate_history_id": 7,
              })),
        patch("services.approval_service.rollback_resume_approval",
              new=rollback or AsyncMock(return_value={"ok": True})),
        patch("services.approval_service.latest_approved_gate",
              new=AsyncMock(return_value=approved_gate)),
        patch("services.template_runner.TemplateRunner", new=runner_cls),
        patch("services.template_runner.has_resumable_checkpoint",
              new=AsyncMock(return_value=has_ckpt)),
        # Mid-graph resume re-threads the full (database_service, platform)
        # handles. Stub the builder so the atomicity test never opens a real
        # pool / builds a real Platform.
        patch("poindexter.cli.pipeline._build_resume_handles",
              new=AsyncMock(return_value=(MagicMock(close=AsyncMock()), MagicMock()))),
        # The CLI resolves the checkpointer DSN via its vendored resolver
        # (brain.bootstrap isn't importable in the installed CLI venv). Stub
        # it so the resume path never reads bootstrap.toml / DB env vars on CI.
        patch("poindexter.cli.pipeline._dsn",
              new=MagicMock(return_value="postgresql://test/dsn")),
    ]
    return patches, runner_cls


def _invoke(bundle):
    patches, _ = bundle
    with ExitStack() as stack:
        for p in patches:
            stack.enter_context(p)
        return CliRunner().invoke(resume_command, [_TID])


# ---------------------------------------------------------------------------
# PATH 1 — approve-and-resume (task paused at a gate)
# ---------------------------------------------------------------------------


class TestApproveAndResume:
    def test_happy_path_approves_then_resumes(self):
        approve = AsyncMock(return_value={
            "ok": True, "task_id": _TID, "gate_name": "draft_gate",
            "gate_history_id": 7,
        })
        rollback = AsyncMock()
        bundle = _patches(
            row=_paused_row(), approve=approve, rollback=rollback,
            run_result=SimpleNamespace(ok=True, halted_at=None),
        )
        result = _invoke(bundle)
        assert result.exit_code == 0, result.output
        approve.assert_awaited_once()
        bundle[1].return_value.run.assert_awaited_once()
        # Happy path must NOT compensate.
        rollback.assert_not_awaited()

    def test_resume_raises_rolls_back_approval(self):
        """THE regression: a raising resume rolls the approval back so the
        task stays re-resumable and no stale approval survives."""
        approve = AsyncMock(return_value={
            "ok": True, "task_id": _TID, "gate_name": "draft_gate",
            "gate_history_id": 7,
        })
        rollback = AsyncMock(return_value={"ok": True, "deleted_row": True})
        bundle = _patches(
            row=_paused_row(), approve=approve, rollback=rollback,
            run_side_effect=RuntimeError("checkpointer setup failed"),
        )
        result = _invoke(bundle)

        assert result.exit_code != 0
        # The approval was compensated: rolled back by id, gate restored.
        rollback.assert_awaited_once()
        kw = rollback.await_args.kwargs
        assert kw["gate_history_id"] == 7
        assert kw["gate_name"] == "draft_gate"
        # The original artifact + paused_at are handed back for restoration.
        assert kw["artifact"] == '{"title": "X"}'
        # Operator is told it's recoverable.
        assert "rolled back" in result.output.lower()

    def test_passes_explicit_checkpointer_dsn_to_runner(self):
        """Regression: the CLI must hand TemplateRunner an explicit
        ``checkpointer_dsn``.

        TemplateRunner's own fallback resolver imports ``brain.bootstrap``,
        which is NOT on sys.path in the installed CLI venv (poindexter-backend
        ships only ``cofounder_agent``). Without the explicit DSN the runner
        swallows the ModuleNotFoundError, degrades to MemorySaver, and the
        resume can't load the durable checkpoint — it re-runs from the entry
        node with no ``post_id`` and halts at ``content.load_existing_post``.
        """
        bundle = _patches(
            row=_paused_row(),
            run_result=SimpleNamespace(ok=True, halted_at=None),
        )
        result = _invoke(bundle)
        assert result.exit_code == 0, result.output
        runner_cls = bundle[1]
        runner_cls.assert_called_once()
        call = runner_cls.call_args
        assert call is not None
        assert call.kwargs.get("checkpointer_dsn") == "postgresql://test/dsn"

    def test_resume_returns_halted_does_not_roll_back(self):
        """A downstream halt / QA-reject is ok=False but the gate WAS passed —
        the approval is legitimately consumed, so we must NOT compensate."""
        rollback = AsyncMock()
        bundle = _patches(
            row=_paused_row(), rollback=rollback,
            run_result=SimpleNamespace(ok=False, halted_at="qa.aggregate"),
        )
        result = _invoke(bundle)
        assert result.exit_code == 0, result.output
        rollback.assert_not_awaited()


# ---------------------------------------------------------------------------
# PATH 2 — continue-resume (stranded past the gate, awaiting_gate cleared)
# ---------------------------------------------------------------------------


class TestContinueResume:
    def test_intact_checkpoint_resumes_without_re_approving(self):
        approve = AsyncMock()  # must NOT be called — already approved
        bundle = _patches(
            row=_paused_row(status="in_progress", awaiting_gate=None),
            approve=approve,
            run_result=SimpleNamespace(ok=True, halted_at=None),
            has_ckpt=True,
            approved_gate="draft_gate",
        )
        result = _invoke(bundle)
        assert result.exit_code == 0, result.output
        approve.assert_not_awaited()
        bundle[1].return_value.run.assert_awaited_once()

    def test_no_checkpoint_reports_nothing_to_resume(self):
        bundle = _patches(
            row=_paused_row(status="in_progress", awaiting_gate=None),
            has_ckpt=False,
            approved_gate=None,
        )
        result = _invoke(bundle)
        assert result.exit_code != 0
        assert "resume" in result.output.lower()
        # Never tried to run the graph.
        bundle[1].return_value.run.assert_not_awaited()


# ---------------------------------------------------------------------------
# Bulk resume — multiple task_ids and --all (cumbersome-backlog fix)
# ---------------------------------------------------------------------------

_TID2 = "def45678-0000-0000-0000-000000000000"


def _bulk_patches(rows: dict, *, run_result=None):
    """Like ``_patches`` but ``_fetch_paused_row`` is keyed per task_id, so a
    bulk batch can mix found/not-found/paused tasks in one invocation."""

    async def _fake_fetch(_pool, task_id):
        return rows.get(task_id)

    runner_cls = MagicMock()
    runner_cls.return_value.run = AsyncMock(
        return_value=run_result or SimpleNamespace(ok=True, halted_at=None)
    )
    patches = [
        patch("poindexter.cli.pipeline._make_pool",
              new=AsyncMock(return_value=MagicMock(close=AsyncMock()))),
        patch("poindexter.cli.pipeline._make_site_config",
              new=AsyncMock(return_value=MagicMock())),
        patch("poindexter.cli.pipeline._fetch_paused_row",
              new=AsyncMock(side_effect=_fake_fetch)),
        patch("services.approval_service.approve",
              new=AsyncMock(return_value={
                  "ok": True, "gate_name": "draft_gate", "gate_history_id": 7,
              })),
        patch("services.approval_service.rollback_resume_approval",
              new=AsyncMock(return_value={"ok": True})),
        patch("services.template_runner.TemplateRunner", new=runner_cls),
        patch("services.template_runner.has_resumable_checkpoint",
              new=AsyncMock(return_value=False)),
        patch("poindexter.cli.pipeline._build_resume_handles",
              new=AsyncMock(return_value=(MagicMock(close=AsyncMock()), MagicMock()))),
        patch("poindexter.cli.pipeline._dsn",
              new=MagicMock(return_value="postgresql://test/dsn")),
    ]
    return patches, runner_cls


class TestBulkResume:
    def test_multiple_task_ids_all_succeed(self):
        rows = {_TID: _paused_row(), _TID2: _paused_row(task_id=_TID2)}
        patches, runner_cls = _bulk_patches(rows)
        with ExitStack() as stack:
            for p in patches:
                stack.enter_context(p)
            result = CliRunner().invoke(resume_command, [_TID, _TID2])

        assert result.exit_code == 0, result.output
        assert runner_cls.return_value.run.await_count == 2
        assert "2 succeeded, 0 failed" in result.output
        assert _TID in result.output
        assert _TID2 in result.output

    def test_one_failure_does_not_block_the_rest_of_the_batch(self):
        rows = {_TID: _paused_row(), _TID2: None}  # second task not found
        patches, runner_cls = _bulk_patches(rows)
        with ExitStack() as stack:
            for p in patches:
                stack.enter_context(p)
            result = CliRunner().invoke(resume_command, [_TID, _TID2])

        # Batch had a failure -> non-zero exit, but the good task still ran.
        assert result.exit_code != 0
        runner_cls.return_value.run.assert_awaited_once()
        assert "1 succeeded, 1 failed" in result.output
        assert f"Task {_TID2} not found" in result.output

    def test_json_output_is_a_list_one_entry_per_task(self):
        rows = {_TID: _paused_row(), _TID2: _paused_row(task_id=_TID2)}
        patches, _ = _bulk_patches(rows)
        with ExitStack() as stack:
            for p in patches:
                stack.enter_context(p)
            result = CliRunner().invoke(
                resume_command, [_TID, _TID2, "--json"],
            )

        assert result.exit_code == 0, result.output
        payload = json.loads(result.output)
        assert isinstance(payload, list)
        assert {r["task_id"] for r in payload} == {_TID, _TID2}

    def test_task_ids_and_all_together_is_an_error(self):
        result = CliRunner().invoke(resume_command, [_TID, "--all"])
        assert result.exit_code != 0
        assert "not both" in result.output.lower()

    def test_no_task_ids_and_no_all_is_an_error(self):
        result = CliRunner().invoke(resume_command, [])
        assert result.exit_code != 0
        assert "--all" in result.output

    def test_all_flag_resumes_every_paused_task(self):
        rows = {_TID: _paused_row(), _TID2: _paused_row(task_id=_TID2)}
        patches, runner_cls = _bulk_patches(rows)
        with ExitStack() as stack:
            for p in patches:
                stack.enter_context(p)
            stack.enter_context(patch(
                "poindexter.cli.pipeline._fetch_paused_rows",
                new=AsyncMock(return_value=[
                    {"task_id": _TID}, {"task_id": _TID2},
                ]),
            ))
            result = CliRunner().invoke(resume_command, ["--all"])

        assert result.exit_code == 0, result.output
        assert runner_cls.return_value.run.await_count == 2

    def test_all_targets_run_under_a_single_event_loop(self):
        """Regression: the batch used to call asyncio.run() once PER TASK,
        tearing down and rebuilding the event loop between tasks. Any
        module caching an async resource at process scope on the
        assumption of one long-lived loop per process (e.g.
        services.revalidation_service's shared httpx.AsyncClient) breaks
        the moment a later task's loop replaces the one the resource was
        born in — RuntimeError: Event loop is closed."""
        rows = {_TID: _paused_row(), _TID2: _paused_row(task_id=_TID2)}
        patches, runner_cls = _bulk_patches(rows)

        real_run = asyncio.run
        call_count = {"n": 0}

        def _counting_run(coro):
            call_count["n"] += 1
            return real_run(coro)

        with ExitStack() as stack:
            for p in patches:
                stack.enter_context(p)
            stack.enter_context(
                patch("poindexter.cli.pipeline.asyncio.run", side_effect=_counting_run)
            )
            result = CliRunner().invoke(resume_command, [_TID, _TID2])

        assert result.exit_code == 0, result.output
        assert call_count["n"] == 1
        assert runner_cls.return_value.run.await_count == 2

    def test_all_flag_with_nothing_paused_is_a_no_op(self):
        with patch(
            "poindexter.cli.pipeline._make_pool",
            new=AsyncMock(return_value=MagicMock(close=AsyncMock())),
        ), patch(
            "poindexter.cli.pipeline._fetch_paused_rows",
            new=AsyncMock(return_value=[]),
        ):
            result = CliRunner().invoke(resume_command, ["--all"])

        assert result.exit_code == 0, result.output
        assert "no paused pipelines" in result.output.lower()
