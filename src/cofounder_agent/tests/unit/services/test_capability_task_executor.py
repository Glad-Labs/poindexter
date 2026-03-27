"""
Unit tests for services/capability_task_executor.py.

Tests cover:
- CapabilityStep.to_dict — serialization
- CapabilityTaskDefinition.to_dict — serialization including nested steps
- StepResult.to_dict — field mapping
- TaskExecutionResult.to_dict — field mapping, completed_at None path
- TaskExecutionResult.progress_percent — 0 steps, partial, all complete
- CapabilityTaskExecutor._resolve_input_reference — literal, $ reference, not-in-context
- CapabilityTaskExecutor._resolve_inputs — full dict resolution
- CapabilityTaskExecutor.execute — success (all steps), first-step failure
- execute_capability_task — convenience wrapper delegates to executor

All registry calls are mocked; no real capabilities, no DB, no I/O.
"""

from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from services.capability_task_executor import (
    CapabilityStep,
    CapabilityTaskDefinition,
    CapabilityTaskExecutor,
    StepResult,
    TaskExecutionResult,
    execute_capability_task,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _step(name: str, output_key: str, order: int = 0, inputs: dict | None = None) -> CapabilityStep:
    return CapabilityStep(
        capability_name=name,
        inputs=inputs or {},
        output_key=output_key,
        order=order,
    )


def _task(steps=None, owner_id="user-1") -> CapabilityTaskDefinition:
    return CapabilityTaskDefinition(
        name="test-task",
        description="desc",
        steps=steps or [],
        owner_id=owner_id,
    )


def _mock_registry(outputs: list) -> MagicMock:
    """Return a mock registry whose execute returns each value in outputs in sequence."""
    registry = MagicMock()
    registry.execute = AsyncMock(side_effect=outputs)
    return registry


# ---------------------------------------------------------------------------
# CapabilityStep
# ---------------------------------------------------------------------------


class TestCapabilityStep:
    def test_to_dict_round_trip(self):
        step = CapabilityStep(
            capability_name="search",
            inputs={"query": "AI"},
            output_key="results",
            order=3,
            metadata={"retries": 1},
        )
        d = step.to_dict()
        assert d["capability_name"] == "search"
        assert d["inputs"] == {"query": "AI"}
        assert d["output_key"] == "results"
        assert d["order"] == 3
        assert d["metadata"] == {"retries": 1}


# ---------------------------------------------------------------------------
# CapabilityTaskDefinition
# ---------------------------------------------------------------------------


class TestCapabilityTaskDefinition:
    def test_to_dict_includes_steps(self):
        step = _step("echo", "out")
        task = CapabilityTaskDefinition(
            id="task-abc",
            name="my-task",
            description="d",
            steps=[step],
            tags=["tag1"],
            owner_id="owner-x",
        )
        d = task.to_dict()
        assert d["id"] == "task-abc"
        assert d["name"] == "my-task"
        assert len(d["steps"]) == 1
        assert d["steps"][0]["capability_name"] == "echo"
        assert d["tags"] == ["tag1"]
        assert d["owner_id"] == "owner-x"
        assert "created_at" in d

    def test_to_dict_default_id_generated(self):
        task = CapabilityTaskDefinition(name="t")
        d = task.to_dict()
        assert len(d["id"]) > 0


# ---------------------------------------------------------------------------
# StepResult
# ---------------------------------------------------------------------------


class TestStepResult:
    def test_to_dict_completed(self):
        sr = StepResult(
            step_index=0,
            capability_name="echo",
            output_key="out",
            output="hello",
            duration_ms=25.5,
            status="completed",
        )
        d = sr.to_dict()
        assert d["step_index"] == 0
        assert d["output"] == "hello"
        assert d["status"] == "completed"
        assert d["error"] is None

    def test_to_dict_failed(self):
        sr = StepResult(
            step_index=1,
            capability_name="bad-cap",
            output_key="out",
            output=None,
            duration_ms=5.0,
            error="boom",
            status="failed",
        )
        d = sr.to_dict()
        assert d["status"] == "failed"
        assert d["error"] == "boom"
        assert d["output"] is None


# ---------------------------------------------------------------------------
# TaskExecutionResult
# ---------------------------------------------------------------------------


class TestTaskExecutionResult:
    def test_progress_percent_no_steps(self):
        r = TaskExecutionResult(task_id="t")
        assert r.progress_percent == 0

    def test_progress_percent_partial(self):
        r = TaskExecutionResult(task_id="t")
        r.step_results = [
            StepResult(0, "a", "o", None, 0, status="completed"),
            StepResult(1, "b", "o2", None, 0, status="failed"),
        ]
        assert r.progress_percent == 50

    def test_progress_percent_all_complete(self):
        r = TaskExecutionResult(task_id="t")
        r.step_results = [
            StepResult(0, "a", "o", None, 0, status="completed"),
            StepResult(1, "b", "o2", None, 0, status="completed"),
        ]
        assert r.progress_percent == 100

    def test_to_dict_completed_at_none(self):
        r = TaskExecutionResult(task_id="t", completed_at=None)
        d = r.to_dict()
        assert d["completed_at"] is None

    def test_to_dict_completed_at_set(self):
        now = datetime.now(timezone.utc)
        r = TaskExecutionResult(task_id="t", completed_at=now)
        d = r.to_dict()
        assert d["completed_at"] == now.isoformat()


# ---------------------------------------------------------------------------
# CapabilityTaskExecutor — input resolution
# ---------------------------------------------------------------------------


class TestInputResolution:
    def setup_method(self):
        self.executor = CapabilityTaskExecutor(registry=MagicMock())

    def test_resolve_literal_string(self):
        assert self.executor._resolve_input_reference("hello", {}) == "hello"

    def test_resolve_reference_present_in_context(self):
        context = {"my_output": "resolved_value"}
        assert self.executor._resolve_input_reference("$my_output", context) == "resolved_value"

    def test_resolve_reference_not_in_context_returns_original(self):
        assert self.executor._resolve_input_reference("$missing_key", {}) == "$missing_key"

    def test_resolve_non_string_passthrough(self):
        assert self.executor._resolve_input_reference(42, {}) == 42
        assert self.executor._resolve_input_reference({"nested": "dict"}, {}) == {"nested": "dict"}

    def test_resolve_inputs_dict(self):
        context = {"prev_output": "data"}
        inputs = {
            "query": "$prev_output",
            "max_results": 10,
            "label": "static",
        }
        resolved = self.executor._resolve_inputs(inputs, context)
        assert resolved["query"] == "data"
        assert resolved["max_results"] == 10
        assert resolved["label"] == "static"


# ---------------------------------------------------------------------------
# CapabilityTaskExecutor.execute — success path
# ---------------------------------------------------------------------------


class TestExecuteSuccess:
    @pytest.mark.asyncio
    async def test_two_step_pipeline_completes(self):
        registry = _mock_registry(["research_result", "final_answer"])
        executor = CapabilityTaskExecutor(registry=registry)

        task = _task(
            steps=[
                _step("research", "research_data", order=0),
                _step("summarise", "summary", order=1, inputs={"text": "$research_data"}),
            ]
        )

        result = await executor.execute(task)

        assert result.status == "completed"
        assert len(result.step_results) == 2
        assert result.step_results[0].status == "completed"
        assert result.step_results[1].status == "completed"
        assert result.final_outputs["research_data"] == "research_result"
        assert result.final_outputs["summary"] == "final_answer"
        assert result.total_duration_ms >= 0
        assert result.completed_at is not None

    @pytest.mark.asyncio
    async def test_steps_executed_in_order(self):
        call_order = []

        async def mock_execute(capability_name, **kwargs):
            call_order.append(capability_name)
            return f"output-{capability_name}"

        registry = MagicMock()
        registry.execute = mock_execute
        executor = CapabilityTaskExecutor(registry=registry)

        # Provide steps out of order to verify ordering
        task = _task(
            steps=[
                _step("second-cap", "s", order=2),
                _step("first-cap", "f", order=1),
            ]
        )

        result = await executor.execute(task)

        assert call_order == ["first-cap", "second-cap"]
        assert result.status == "completed"

    @pytest.mark.asyncio
    async def test_empty_task_completes_immediately(self):
        registry = _mock_registry([])
        executor = CapabilityTaskExecutor(registry=registry)

        result = await executor.execute(_task(steps=[]))

        assert result.status == "completed"
        assert result.step_results == []
        assert result.final_outputs == {}


# ---------------------------------------------------------------------------
# CapabilityTaskExecutor.execute — failure path
# ---------------------------------------------------------------------------


class TestExecuteFailure:
    @pytest.mark.asyncio
    async def test_first_step_failure_stops_pipeline(self):
        registry = MagicMock()
        registry.execute = AsyncMock(side_effect=RuntimeError("capability exploded"))
        executor = CapabilityTaskExecutor(registry=registry)

        task = _task(
            steps=[
                _step("failing-cap", "out1", order=0),
                _step("never-run-cap", "out2", order=1),
            ]
        )

        result = await executor.execute(task)

        assert result.status == "failed"
        assert result.error is not None and "failing-cap" in result.error
        assert len(result.step_results) == 1
        assert result.step_results[0].status == "failed"
        assert (
            result.step_results[0].error is not None
            and "capability exploded" in result.step_results[0].error
        )
        # Second capability must NOT have been called
        assert registry.execute.call_count == 1

    @pytest.mark.asyncio
    async def test_completed_at_set_even_on_failure(self):
        registry = MagicMock()
        registry.execute = AsyncMock(side_effect=ValueError("bad"))
        executor = CapabilityTaskExecutor(registry=registry)

        result = await executor.execute(_task(steps=[_step("cap", "out")]))

        assert result.completed_at is not None
        assert result.total_duration_ms >= 0


# ---------------------------------------------------------------------------
# execute_capability_task convenience function
# ---------------------------------------------------------------------------


class TestExecuteCapabilityTaskFunction:
    @pytest.mark.asyncio
    async def test_delegates_to_executor(self):
        mock_result = TaskExecutionResult(task_id="t", status="completed")

        with patch(
            "services.capability_task_executor.CapabilityTaskExecutor.execute",
            AsyncMock(return_value=mock_result),
        ):
            task = _task(steps=[])
            result = await execute_capability_task(task)

        assert result.status == "completed"
