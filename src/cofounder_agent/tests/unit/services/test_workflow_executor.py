"""
Unit tests for services/workflow_executor.py.

Tests cover:
- WorkflowExecutor.execute_workflow — phase sequencing and data flow
- WorkflowExecutor._prepare_phase_inputs — input merging strategies
- WorkflowExecutor._execute_phase — agent dispatch and result wrapping
- WorkflowExecutor._normalize_phases — phase coercion from dict/object
- WorkflowExecutor._get_agent — mapping and import
- WorkflowExecutionError raised on phase failure

PhaseRegistry, PhaseMapper, and build_full_phase_pipeline are mocked.
"""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from schemas.custom_workflow_schemas import CustomWorkflow, WorkflowPhase
from services.workflow_executor import WorkflowExecutionError, WorkflowExecutor

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_workflow(phases=None):
    """Build a minimal CustomWorkflow with one or more phases."""
    return CustomWorkflow(  # type: ignore[call-arg]
        name="Test Workflow",
        description="A unit test workflow",
        phases=phases
        or [
            WorkflowPhase(index=0, name="research", user_inputs={"topic": "AI trends"}),  # type: ignore[call-arg]
        ],
    )


def _make_phase_def(agent_type="research_agent"):
    """Mock PhaseDefinition returned by PhaseRegistry.get_phase()."""
    pd = MagicMock()
    pd.agent_type = agent_type
    pd.input_schema = {}  # No required defaults
    return pd


def _make_registry(phase_def=None):
    """Mock PhaseRegistry."""
    reg = MagicMock()
    reg.get_phase = MagicMock(return_value=phase_def or _make_phase_def())
    reg.get_instance = MagicMock(return_value=reg)
    return reg


def _make_mapper():
    """Mock PhaseMapper."""
    mapper = MagicMock()
    return mapper


def _make_agent(status="success", output=None):
    """Mock agent with async run() method."""
    agent = MagicMock()
    agent.run = AsyncMock(
        return_value={"status": status, **(output or {"content": "Generated content"})}
    )
    return agent


def _make_executor(registry=None, mapper=None):
    """Build WorkflowExecutor with mocked dependencies."""
    reg = registry or _make_registry()
    mpr = mapper or _make_mapper()
    executor = WorkflowExecutor(registry=reg, mapper=mpr)
    return executor, reg, mpr


# ---------------------------------------------------------------------------
# WorkflowExecutor.execute_workflow
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestExecuteWorkflow:
    @pytest.mark.asyncio
    async def test_single_phase_returns_results(self):
        executor, reg, _ = _make_executor()
        workflow = _make_workflow()
        agent = _make_agent()

        with (
            patch(
                "services.workflow_executor.build_full_phase_pipeline",
                return_value={"research": {}},
            ),
            patch.object(executor, "_get_agent", return_value=agent),
        ):
            results = await executor.execute_workflow(workflow, initial_inputs={"topic": "AI"})

        assert "research" in results
        assert results["research"].status == "completed"

    @pytest.mark.asyncio
    async def test_two_phase_workflow_executes_both(self):
        executor, reg, _ = _make_executor()
        workflow = _make_workflow(
            phases=[
                WorkflowPhase(index=0, name="research", user_inputs={"topic": "AI"}),  # type: ignore[call-arg]
                WorkflowPhase(index=1, name="draft", user_inputs={}),  # type: ignore[call-arg]
            ]
        )
        agent = _make_agent()

        with (
            patch(
                "services.workflow_executor.build_full_phase_pipeline",
                return_value={"research": {}, "draft": {}},
            ),
            patch.object(executor, "_get_agent", return_value=agent),
        ):
            results = await executor.execute_workflow(workflow)

        assert "research" in results
        assert "draft" in results

    @pytest.mark.asyncio
    async def test_skipped_phase_produces_skipped_result(self):
        executor, reg, _ = _make_executor()
        workflow = _make_workflow(
            phases=[
                WorkflowPhase(index=0, name="research", user_inputs={}, skip=True),  # type: ignore[call-arg]
            ]
        )

        with patch(
            "services.workflow_executor.build_full_phase_pipeline",
            return_value={"research": {}},
        ):
            results = await executor.execute_workflow(workflow)

        # Skipped phase is not recorded (continue without adding to results)
        assert "research" not in results or results["research"].status == "skipped"

    @pytest.mark.asyncio
    async def test_failed_phase_returns_failed_result(self):
        """
        A failed phase does NOT raise — execute_workflow catches the internal
        WorkflowExecutionError and marks remaining phases as skipped, then
        returns the phase_results dict normally.
        """
        executor, reg, _ = _make_executor()
        workflow = _make_workflow()
        agent = _make_agent(status="failed")

        with (
            patch(
                "services.workflow_executor.build_full_phase_pipeline",
                return_value={"research": {}},
            ),
            patch.object(executor, "_get_agent", return_value=agent),
        ):
            results = await executor.execute_workflow(workflow)

        # The phase result is recorded as failed (or the phase is skipped via halt path)
        assert "research" in results
        assert results["research"].status in ("failed", "skipped")

    @pytest.mark.asyncio
    async def test_phase_mapping_error_raises_workflow_execution_error(self):
        from services.phase_mapper import PhaseMappingError

        executor, _, _ = _make_executor()
        workflow = _make_workflow()

        with patch(
            "services.workflow_executor.build_full_phase_pipeline",
            side_effect=PhaseMappingError("bad mapping"),
        ):
            with pytest.raises(WorkflowExecutionError):
                await executor.execute_workflow(workflow)

    @pytest.mark.asyncio
    async def test_progress_service_called_on_phase_start_and_complete(self):
        executor, reg, _ = _make_executor()
        workflow = _make_workflow()
        agent = _make_agent()
        progress_svc = MagicMock()

        with (
            patch(
                "services.workflow_executor.build_full_phase_pipeline",
                return_value={"research": {}},
            ),
            patch.object(executor, "_get_agent", return_value=agent),
        ):
            await executor.execute_workflow(
                workflow, initial_inputs={"topic": "AI"}, progress_service=progress_svc
            )

        progress_svc.start_phase.assert_called_once()
        progress_svc.complete_phase.assert_called_once()

    @pytest.mark.asyncio
    async def test_custom_execution_id_used(self):
        executor, _, _ = _make_executor()
        workflow = _make_workflow()
        agent = _make_agent()

        with (
            patch(
                "services.workflow_executor.build_full_phase_pipeline",
                return_value={"research": {}},
            ),
            patch.object(executor, "_get_agent", return_value=agent),
        ):
            results = await executor.execute_workflow(workflow, execution_id="custom-exec-id-123")

        assert "research" in results


# ---------------------------------------------------------------------------
# WorkflowExecutor._prepare_phase_inputs
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestPreparePhaseInputs:
    def test_first_phase_gets_initial_inputs(self):
        executor, _, _ = _make_executor()
        phase = WorkflowPhase(index=0, name="research", user_inputs={})  # type: ignore[call-arg]

        inputs, traces = executor._prepare_phase_inputs(
            phase=phase,
            phase_index=0,
            initial_inputs={"topic": "AI trends"},
            previous_outputs={},
            phase_mapping={},
        )

        assert inputs["topic"] == "AI trends"
        assert traces["topic"].user_provided is True

    def test_user_inputs_override_auto_mapped(self):
        """User-provided inputs take priority over auto-mapping."""
        executor, _, _ = _make_executor()
        phase = WorkflowPhase(index=1, name="draft", user_inputs={"topic": "Overridden"})  # type: ignore[call-arg]

        inputs, traces = executor._prepare_phase_inputs(
            phase=phase,
            phase_index=1,
            initial_inputs={},
            previous_outputs={"research": {"topic": "Auto topic"}},
            phase_mapping={"topic": "topic"},
        )

        assert inputs["topic"] == "Overridden"
        assert traces["topic"].user_provided is True

    def test_auto_mapping_from_previous_phase(self):
        executor, _, _ = _make_executor()
        phase = WorkflowPhase(index=1, name="draft", user_inputs={})  # type: ignore[call-arg]

        inputs, traces = executor._prepare_phase_inputs(
            phase=phase,
            phase_index=1,
            initial_inputs={},
            previous_outputs={"research": {"research_output": "Some research"}},
            phase_mapping={"content": "research_output"},
        )

        assert inputs["content"] == "Some research"
        assert traces["content"].auto_mapped is True
        assert traces["content"].source_phase == "research"

    def test_no_auto_mapping_for_first_phase(self):
        """First phase (index=0) should not auto-map from previous outputs."""
        executor, _, _ = _make_executor()
        phase = WorkflowPhase(index=0, name="research", user_inputs={})  # type: ignore[call-arg]

        inputs, traces = executor._prepare_phase_inputs(
            phase=phase,
            phase_index=0,
            initial_inputs={},
            previous_outputs={"earlier": {"key": "value"}},
            phase_mapping={"key": "key"},
        )

        # Should not auto-map on first phase (no "earlier" auto-mapping)
        assert "key" not in inputs

    def test_defaults_from_phase_definition_filled(self):
        """Phase definition defaults are applied when not overridden."""
        pd = _make_phase_def()
        default_field = MagicMock()
        default_field.default_value = "default_val"
        pd.input_schema = {"style": default_field}

        executor, reg, _ = _make_executor(registry=_make_registry(phase_def=pd))
        phase = WorkflowPhase(index=0, name="research", user_inputs={})  # type: ignore[call-arg]

        inputs, traces = executor._prepare_phase_inputs(
            phase=phase,
            phase_index=0,
            initial_inputs={},
            previous_outputs={},
            phase_mapping={},
        )

        assert inputs["style"] == "default_val"


# ---------------------------------------------------------------------------
# WorkflowExecutor._execute_phase
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestExecutePhase:
    @pytest.mark.asyncio
    async def test_returns_completed_when_agent_succeeds(self):
        agent = _make_agent(status="success")
        executor, reg, _ = _make_executor()
        phase = WorkflowPhase(index=0, name="research", user_inputs={})  # type: ignore[call-arg]

        with patch.object(executor, "_get_agent", return_value=agent):
            result = await executor._execute_phase(phase, {"topic": "AI"}, "exec-1")

        assert result.status == "completed"

    @pytest.mark.asyncio
    async def test_returns_failed_when_agent_returns_failed_status(self):
        agent = _make_agent(status="failed")
        executor, _, _ = _make_executor()
        phase = WorkflowPhase(index=0, name="research", user_inputs={})  # type: ignore[call-arg]

        with patch.object(executor, "_get_agent", return_value=agent):
            result = await executor._execute_phase(phase, {}, "exec-1")

        assert result.status == "failed"

    @pytest.mark.asyncio
    async def test_returns_failed_when_phase_not_in_registry(self):
        executor, reg, _ = _make_executor()
        reg.get_phase = MagicMock(return_value=None)
        phase = WorkflowPhase(index=0, name="unknown_phase", user_inputs={})  # type: ignore[call-arg]

        result = await executor._execute_phase(phase, {}, "exec-1")

        assert result.status == "failed"
        assert "not found" in (result.error or "")

    @pytest.mark.asyncio
    async def test_returns_failed_when_agent_not_found(self):
        executor, _, _ = _make_executor()
        phase = WorkflowPhase(index=0, name="research", user_inputs={})  # type: ignore[call-arg]

        with patch.object(executor, "_get_agent", return_value=None):
            result = await executor._execute_phase(phase, {}, "exec-1")

        assert result.status == "failed"
        assert "not found" in (result.error or "")

    @pytest.mark.asyncio
    async def test_agent_exception_returns_failed_result(self):
        agent = MagicMock()
        agent.run = AsyncMock(side_effect=RuntimeError("LLM timeout"))
        executor, _, _ = _make_executor()
        phase = WorkflowPhase(index=0, name="research", user_inputs={})  # type: ignore[call-arg]

        with patch.object(executor, "_get_agent", return_value=agent):
            result = await executor._execute_phase(phase, {}, "exec-1")

        assert result.status == "failed"
        assert "LLM timeout" in (result.error or "")

    @pytest.mark.asyncio
    async def test_non_success_non_failed_agent_status_returns_failed(self):
        """Regression: both branches of the ternary previously returned 'completed'.
        Any status that is not 'success' and not 'failed' (e.g. 'partial', 'timeout')
        must now produce a 'failed' PhaseResult (issue #664)."""
        agent = _make_agent(status="partial")
        executor, _, _ = _make_executor()
        phase = WorkflowPhase(index=0, name="research", user_inputs={})  # type: ignore[call-arg]

        with patch.object(executor, "_get_agent", return_value=agent):
            result = await executor._execute_phase(phase, {"topic": "AI"}, "exec-1")

        assert result.status == "failed"


# ---------------------------------------------------------------------------
# WorkflowExecutor._normalize_phases
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestNormalizePhases:
    def test_workflow_phase_objects_unchanged(self):
        executor, _, _ = _make_executor()
        phases = [WorkflowPhase(index=0, name="research", user_inputs={})]  # type: ignore[call-arg]
        normalized = executor._normalize_phases(phases)
        assert len(normalized) == 1
        assert normalized[0].name == "research"

    def test_dict_phases_converted_to_workflow_phase(self):
        executor, _, _ = _make_executor()
        phases = [{"index": 0, "name": "research", "user_inputs": {}, "skip": False}]
        normalized = executor._normalize_phases(phases)
        assert len(normalized) == 1
        assert isinstance(normalized[0], WorkflowPhase)
        assert normalized[0].name == "research"

    def test_dict_without_index_gets_index_assigned(self):
        executor, _, _ = _make_executor()
        phases = [{"name": "research", "user_inputs": {}, "skip": False}]
        normalized = executor._normalize_phases(phases)
        assert normalized[0].index == 0

    def test_workflow_phase_without_index_gets_index_assigned(self):
        executor, _, _ = _make_executor()
        phase = WorkflowPhase(index=0, name="research", user_inputs={})  # type: ignore[call-arg]
        phase.index = None  # type: ignore[assignment]
        normalized = executor._normalize_phases([phase])
        assert normalized[0].index == 0


# ---------------------------------------------------------------------------
# WorkflowExecutor._get_agent
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestGetAgent:
    def test_unknown_agent_type_returns_none(self):
        executor, _, _ = _make_executor()
        result = executor._get_agent("completely_unknown_agent_xyz")
        assert result is None

    def test_import_error_returns_none(self):
        executor, _, _ = _make_executor()
        with patch("importlib.import_module", side_effect=ImportError("no module")):
            result = executor._get_agent("blog_content_generator_agent")
        assert result is None

    def test_known_agent_type_attempts_import(self):
        executor, _, _ = _make_executor()
        mock_agent = MagicMock()
        mock_module = MagicMock()
        mock_module.get_blog_quality_agent = MagicMock(return_value=mock_agent)

        with patch("importlib.import_module", return_value=mock_module):
            result = executor._get_agent("blog_quality_agent")

        assert result is mock_agent
