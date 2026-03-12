"""
Unit tests for services/workflow_engine.py.

Tests cover:
- WorkflowEngine.execute_workflow — phase sequencing, failure handling, status transitions
- WorkflowEngine._execute_phase — retry logic, timeout, success/failure paths
- WorkflowEngine.pause_workflow / resume_workflow / cancel_workflow — lifecycle controls
- WorkflowEngine.get_workflow_status — retrieval after execution
- WorkflowContext helpers — get_phase_result, set/get_variable, has_failures, to_dict
- PhaseResult.to_dict — serialization

WebSocketEventBroadcaster is patched to avoid real network I/O.
"""

import asyncio
import pytest
from unittest.mock import AsyncMock, MagicMock, patch

from services.workflow_engine import (
    PhaseResult,
    PhaseStatus,
    WorkflowContext,
    WorkflowEngine,
    WorkflowPhase,
    WorkflowStatus,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_context(workflow_id="wf-1", request_id="req-1"):
    return WorkflowContext(
        workflow_id=workflow_id,
        request_id=request_id,
        initial_input={"topic": "AI"},
    )


def _make_phase(name="research", handler=None, required=True, max_retries=0, skip_on_error=False):
    async def _default_handler(ctx):
        return {"output": f"{name}_result"}

    return WorkflowPhase(
        name=name,
        handler=handler or _default_handler,
        max_retries=max_retries,
        required=required,
        skip_on_error=skip_on_error,
    )


def _make_engine(database_service=None):
    return WorkflowEngine(database_service=database_service, enable_training_data=False)


# Patch WebSocketEventBroadcaster so broadcast calls never raise
_BROADCASTER_PATCH = patch(
    "services.workflow_engine.WebSocketEventBroadcaster.broadcast_workflow_status",
    new=AsyncMock(),
)


# ---------------------------------------------------------------------------
# WorkflowEngine.execute_workflow
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestExecuteWorkflow:
    @pytest.mark.asyncio
    async def test_single_phase_returns_completed(self):
        engine = _make_engine()
        phase = _make_phase()
        ctx = _make_context()

        with _BROADCASTER_PATCH:
            result_ctx = await engine.execute_workflow([phase], ctx)

        assert result_ctx.status == WorkflowStatus.COMPLETED
        assert "research" in result_ctx.results
        assert result_ctx.results["research"].status == PhaseStatus.COMPLETED

    @pytest.mark.asyncio
    async def test_two_phases_both_execute(self):
        engine = _make_engine()
        phases = [_make_phase("research"), _make_phase("draft")]
        ctx = _make_context()

        with _BROADCASTER_PATCH:
            result_ctx = await engine.execute_workflow(phases, ctx)

        assert "research" in result_ctx.phases_executed
        assert "draft" in result_ctx.phases_executed
        assert result_ctx.status == WorkflowStatus.COMPLETED

    @pytest.mark.asyncio
    async def test_required_phase_failure_stops_workflow(self):
        engine = _make_engine()

        async def failing_handler(ctx):
            raise RuntimeError("LLM failure")

        phases = [
            _make_phase("research", handler=failing_handler, required=True),
            _make_phase("draft"),
        ]
        ctx = _make_context()

        with _BROADCASTER_PATCH:
            result_ctx = await engine.execute_workflow(phases, ctx)

        assert result_ctx.status == WorkflowStatus.FAILED
        # Second phase should not have run
        assert "draft" not in result_ctx.phases_executed

    @pytest.mark.asyncio
    async def test_optional_phase_failure_continues_workflow(self):
        engine = _make_engine()

        async def failing_handler(ctx):
            raise RuntimeError("optional step failed")

        phases = [
            _make_phase("research", handler=failing_handler, required=False),
            _make_phase("draft"),
        ]
        ctx = _make_context()

        with _BROADCASTER_PATCH:
            result_ctx = await engine.execute_workflow(phases, ctx)

        # Draft should still execute even though research failed
        assert "draft" in result_ctx.phases_executed

    @pytest.mark.asyncio
    async def test_phase_output_stored_in_context(self):
        engine = _make_engine()

        async def handler(ctx):
            return {"blog_content": "Hello world"}

        phase = _make_phase("draft", handler=handler)
        ctx = _make_context()

        with _BROADCASTER_PATCH:
            result_ctx = await engine.execute_workflow([phase], ctx)

        assert result_ctx.results["draft"].output == {"blog_content": "Hello world"}

    @pytest.mark.asyncio
    async def test_empty_phases_returns_completed(self):
        engine = _make_engine()
        ctx = _make_context()

        with _BROADCASTER_PATCH:
            result_ctx = await engine.execute_workflow([], ctx)

        assert result_ctx.status == WorkflowStatus.COMPLETED

    @pytest.mark.asyncio
    async def test_workflow_stored_after_execution(self):
        engine = _make_engine()
        phase = _make_phase()
        ctx = _make_context(workflow_id="wf-stored")

        with _BROADCASTER_PATCH:
            await engine.execute_workflow([phase], ctx)

        assert "wf-stored" in engine.executed_workflows

    @pytest.mark.asyncio
    async def test_broadcaster_error_does_not_fail_workflow(self):
        """WebSocket broadcast errors are caught; workflow still completes."""
        engine = _make_engine()
        phase = _make_phase()
        ctx = _make_context()

        with patch(
            "services.workflow_engine.WebSocketEventBroadcaster.broadcast_workflow_status",
            new=AsyncMock(side_effect=RuntimeError("WS down")),
        ):
            result_ctx = await engine.execute_workflow([phase], ctx)

        assert result_ctx.status == WorkflowStatus.COMPLETED


# ---------------------------------------------------------------------------
# WorkflowEngine._execute_phase — retry logic
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestExecutePhase:
    @pytest.mark.asyncio
    async def test_success_on_first_attempt(self):
        engine = _make_engine()
        phase = _make_phase(max_retries=2)
        ctx = _make_context()

        with _BROADCASTER_PATCH:
            result = await engine._execute_phase(phase, ctx)

        assert result.status == PhaseStatus.COMPLETED
        assert result.retry_count == 0

    @pytest.mark.asyncio
    async def test_retry_eventually_succeeds(self):
        engine = _make_engine()
        call_count = 0

        async def flaky_handler(ctx):
            nonlocal call_count
            call_count += 1
            if call_count < 3:
                raise RuntimeError("transient error")
            return {"output": "ok"}

        phase = _make_phase("research", handler=flaky_handler, max_retries=3)
        ctx = _make_context()

        with (
            _BROADCASTER_PATCH,
            patch("asyncio.sleep", new=AsyncMock()),
        ):
            result = await engine._execute_phase(phase, ctx)

        assert result.status == PhaseStatus.COMPLETED
        assert call_count == 3

    @pytest.mark.asyncio
    async def test_all_retries_exhausted_returns_failed(self):
        engine = _make_engine()

        async def always_fails(ctx):
            raise RuntimeError("always fails")

        phase = _make_phase("research", handler=always_fails, max_retries=2)
        ctx = _make_context()

        with (
            _BROADCASTER_PATCH,
            patch("asyncio.sleep", new=AsyncMock()),
        ):
            result = await engine._execute_phase(phase, ctx)

        assert result.status == PhaseStatus.FAILED
        assert "always fails" in (result.error or "")

    @pytest.mark.asyncio
    async def test_timeout_returns_failed(self):
        """Phase that exceeds its timeout should produce a FAILED result.
        Uses asyncio.Event (not asyncio.sleep) so the mock does not inadvertently
        resolve the blocking call early.
        """
        engine = _make_engine()
        block = asyncio.Event()

        async def blocking_handler(ctx):
            # Wait indefinitely — only released by the event (which we never set)
            await asyncio.wait_for(block.wait(), timeout=9999)

        phase = WorkflowPhase(
            name="slow",
            handler=blocking_handler,
            timeout_seconds=0.01,  # 10ms — fast enough for CI  # type: ignore[arg-type]
            max_retries=0,
        )
        ctx = _make_context()

        with _BROADCASTER_PATCH:
            result = await engine._execute_phase(phase, ctx)

        assert result.status == PhaseStatus.FAILED
        assert result.error is not None

    @pytest.mark.asyncio
    async def test_custom_error_handler_called_on_failure(self):
        error_handler = AsyncMock()
        engine = WorkflowEngine(error_handler=error_handler, enable_training_data=False)

        async def bad_handler(ctx):
            raise ValueError("bad input")

        phase = _make_phase("research", handler=bad_handler, max_retries=0)
        ctx = _make_context()

        with (
            _BROADCASTER_PATCH,
            patch("asyncio.sleep", new=AsyncMock()),
        ):
            await engine._execute_phase(phase, ctx)

        error_handler.assert_awaited_once()


# ---------------------------------------------------------------------------
# Lifecycle controls: pause / resume / cancel
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestLifecycleControls:
    @pytest.mark.asyncio
    async def test_pause_running_workflow(self):
        engine = _make_engine()
        ctx = _make_context(workflow_id="wf-pause")
        ctx.status = WorkflowStatus.RUNNING
        engine.executed_workflows["wf-pause"] = ctx

        result = engine.pause_workflow("wf-pause")

        assert result is True
        assert ctx.status == WorkflowStatus.PAUSED

    def test_pause_unknown_workflow_returns_false(self):
        engine = _make_engine()
        assert engine.pause_workflow("does-not-exist") is False

    def test_pause_already_completed_returns_false(self):
        engine = _make_engine()
        ctx = _make_context("wf-done")
        ctx.status = WorkflowStatus.COMPLETED
        engine.executed_workflows["wf-done"] = ctx

        assert engine.pause_workflow("wf-done") is False

    def test_resume_paused_workflow(self):
        engine = _make_engine()
        ctx = _make_context("wf-paused")
        ctx.status = WorkflowStatus.PAUSED
        engine.executed_workflows["wf-paused"] = ctx

        result = engine.resume_workflow("wf-paused")

        assert result is True
        assert ctx.status == WorkflowStatus.RUNNING

    def test_resume_non_paused_returns_false(self):
        engine = _make_engine()
        ctx = _make_context("wf-running")
        ctx.status = WorkflowStatus.RUNNING
        engine.executed_workflows["wf-running"] = ctx

        assert engine.resume_workflow("wf-running") is False

    def test_cancel_running_workflow(self):
        engine = _make_engine()
        ctx = _make_context("wf-cancel")
        ctx.status = WorkflowStatus.RUNNING
        engine.executed_workflows["wf-cancel"] = ctx

        result = engine.cancel_workflow("wf-cancel")

        assert result is True
        assert ctx.status == WorkflowStatus.CANCELLED

    def test_cancel_paused_workflow(self):
        engine = _make_engine()
        ctx = _make_context("wf-cancel2")
        ctx.status = WorkflowStatus.PAUSED
        engine.executed_workflows["wf-cancel2"] = ctx

        assert engine.cancel_workflow("wf-cancel2") is True

    def test_cancel_completed_workflow_returns_false(self):
        engine = _make_engine()
        ctx = _make_context("wf-complete")
        ctx.status = WorkflowStatus.COMPLETED
        engine.executed_workflows["wf-complete"] = ctx

        assert engine.cancel_workflow("wf-complete") is False

    def test_get_workflow_status_known(self):
        engine = _make_engine()
        ctx = _make_context("wf-known")
        ctx.status = WorkflowStatus.COMPLETED
        engine.executed_workflows["wf-known"] = ctx

        status = engine.get_workflow_status("wf-known")

        assert status is not None
        assert status["workflow_id"] == "wf-known"
        assert status["status"] == "completed"

    def test_get_workflow_status_unknown_returns_none(self):
        engine = _make_engine()
        assert engine.get_workflow_status("unknown") is None


# ---------------------------------------------------------------------------
# WorkflowContext helpers
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestWorkflowContext:
    def test_get_phase_result_existing(self):
        ctx = _make_context()
        result = PhaseResult(phase_name="research", status=PhaseStatus.COMPLETED)
        ctx.results["research"] = result
        assert ctx.get_phase_result("research") is result

    def test_get_phase_result_missing_returns_none(self):
        ctx = _make_context()
        assert ctx.get_phase_result("no-such-phase") is None

    def test_set_and_get_variable(self):
        ctx = _make_context()
        ctx.set_variable("blog_title", "My Post")
        assert ctx.get_variable("blog_title") == "My Post"

    def test_get_variable_default(self):
        ctx = _make_context()
        assert ctx.get_variable("missing", default=42) == 42

    def test_has_failures_with_failed_phase(self):
        ctx = _make_context()
        ctx.results["research"] = PhaseResult(
            phase_name="research", status=PhaseStatus.FAILED
        )
        assert ctx.has_failures() is True

    def test_has_failures_no_failures(self):
        ctx = _make_context()
        ctx.results["research"] = PhaseResult(
            phase_name="research", status=PhaseStatus.COMPLETED
        )
        assert ctx.has_failures() is False

    def test_has_failures_include_skipped(self):
        ctx = _make_context()
        ctx.results["research"] = PhaseResult(
            phase_name="research", status=PhaseStatus.SKIPPED
        )
        assert ctx.has_failures(include_skipped=True) is True
        assert ctx.has_failures(include_skipped=False) is False

    def test_to_dict_has_expected_keys(self):
        ctx = _make_context()
        d = ctx.to_dict()
        for key in ["workflow_id", "request_id", "status", "phases_executed", "results"]:
            assert key in d


# ---------------------------------------------------------------------------
# PhaseResult.to_dict
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestPhaseResultToDict:
    def test_completed_result_serialized(self):
        result = PhaseResult(
            phase_name="research",
            status=PhaseStatus.COMPLETED,
            output={"data": "here"},
            retry_count=1,
        )
        d = result.to_dict()
        assert d["phase_name"] == "research"
        assert d["status"] == "completed"
        assert d["output"] == {"data": "here"}
        assert d["retry_count"] == 1
        assert d["completed_at"] is None  # Not set explicitly

    def test_failed_result_has_error(self):
        result = PhaseResult(
            phase_name="research",
            status=PhaseStatus.FAILED,
            error="LLM timeout",
        )
        d = result.to_dict()
        assert d["status"] == "failed"
        assert d["error"] == "LLM timeout"
