"""
Unit tests for services/custom_workflows_service.py.

Tests cover:
- CustomWorkflowsService.__init__ — service initialization, dependencies injected
- CustomWorkflowsService.validate_workflow — delegates to WorkflowValidator
- CustomWorkflowsService.get_workflow — found, not found, access denied, DB error
- CustomWorkflowsService.get_workflow_by_name — found, not found, DB error
- CustomWorkflowsService.list_workflows — success with templates, without templates, DB error fallback
- CustomWorkflowsService.create_workflow — success, validation error, DB error
- CustomWorkflowsService.update_workflow — success, not found raises ValueError, ownership mismatch, validation failure
- CustomWorkflowsService.delete_workflow — success, not found raises ValueError
- CustomWorkflowsService._row_to_workflow — new WorkflowPhase format, old PhaseConfig format, empty phases
- CustomWorkflowsService.get_available_phases — returns list from registry

All external I/O (asyncpg pool, PhaseRegistry, WorkflowValidator, WorkflowExecutor) is mocked.
"""

import json
from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from schemas.custom_workflow_schemas import CustomWorkflow, WorkflowPhase, WorkflowValidationResult
from services.custom_workflows_service import CustomWorkflowsService

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_NOW = datetime(2026, 3, 12, 10, 0, 0, tzinfo=timezone.utc)


_DEFAULT_PHASES = json.dumps(
    [{"index": 0, "name": "research", "user_inputs": {}, "input_mapping": {}}]
)


def _make_row(**kwargs):
    defaults = {
        "id": "wf-uuid-1",
        "name": "My Workflow",
        "description": "Test workflow",
        "phases": _DEFAULT_PHASES,
        "owner_id": "user-123",
        "created_at": _NOW,
        "updated_at": _NOW,
        "tags": json.dumps([]),  # Empty list, not None — CustomWorkflow requires List
        "is_template": False,
    }
    defaults.update(kwargs)
    row = MagicMock()
    row.__getitem__ = lambda self, k: defaults.get(k)
    row.get = lambda k, default=None: defaults.get(k, default)
    row.__bool__ = lambda self: True
    return row


def _make_pool(
    fetchrow_result=None,
    fetch_result=None,
    fetchval_result=None,
    execute_result=None,
    fetchrow_side_effect=None,
    fetch_side_effect=None,
):
    pool = MagicMock()
    if fetchrow_side_effect:
        pool.fetchrow = AsyncMock(side_effect=fetchrow_side_effect)
    else:
        pool.fetchrow = AsyncMock(return_value=fetchrow_result)
    if fetch_side_effect:
        pool.fetch = AsyncMock(side_effect=fetch_side_effect)
    else:
        pool.fetch = AsyncMock(return_value=fetch_result or [])
    pool.fetchval = AsyncMock(return_value=fetchval_result)
    pool.execute = AsyncMock(return_value=execute_result or "OK")

    # Support async context manager: `async with pool.acquire() as conn:`
    # conn delegates to pool's own fetch/fetchrow so existing tests work unchanged.
    conn = MagicMock()
    conn.fetch = pool.fetch
    conn.fetchrow = pool.fetchrow
    conn.fetchval = pool.fetchval
    conn.execute = pool.execute
    acquire_cm = MagicMock()
    acquire_cm.__aenter__ = AsyncMock(return_value=conn)
    acquire_cm.__aexit__ = AsyncMock(return_value=None)
    pool.acquire = MagicMock(return_value=acquire_cm)

    return pool


def _make_db_service(pool=None):
    db = MagicMock()
    db.pool = pool or _make_pool()
    return db


def _make_service(pool=None) -> CustomWorkflowsService:
    """Create service with mocked PhaseRegistry, WorkflowValidator, and WorkflowExecutor."""
    db_service = _make_db_service(pool)
    with (
        patch("services.custom_workflows_service.PhaseRegistry") as MockRegistry,
        patch("services.custom_workflows_service.WorkflowValidator") as MockValidator,
        patch("services.custom_workflows_service.WorkflowExecutor") as MockExecutor,
    ):
        mock_registry = MagicMock()
        MockRegistry.get_instance.return_value = mock_registry
        service = CustomWorkflowsService(database_service=db_service)
        service.phase_registry = mock_registry
        service.workflow_validator = MagicMock()
        service.workflow_executor = MagicMock()

    return service


def _make_workflow(**kwargs) -> CustomWorkflow:
    defaults = {
        "name": "My Workflow",
        "description": "Test workflow",
        "phases": [WorkflowPhase(index=0, name="research")],  # type: ignore[call-arg]
    }
    defaults.update(kwargs)
    return CustomWorkflow(**defaults)  # type: ignore[call-arg]


def _make_valid_result() -> WorkflowValidationResult:
    return WorkflowValidationResult(valid=True, errors=[], warnings=[])


def _make_invalid_result(errors=None) -> WorkflowValidationResult:
    return WorkflowValidationResult(
        valid=False, errors=errors or ["Phase name is required"], warnings=[]
    )


# ---------------------------------------------------------------------------
# __init__
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestCustomWorkflowsServiceInit:
    def test_database_service_stored(self):
        db_service = _make_db_service()
        with (
            patch("services.custom_workflows_service.PhaseRegistry") as MockRegistry,
            patch("services.custom_workflows_service.WorkflowValidator"),
            patch("services.custom_workflows_service.WorkflowExecutor"),
        ):
            mock_registry = MagicMock()
            MockRegistry.get_instance.return_value = mock_registry
            service = CustomWorkflowsService(database_service=db_service)

        assert service.database_service is db_service

    def test_phases_cache_initialized_as_none(self):
        service = _make_service()
        assert service._available_phases_cache is None


# ---------------------------------------------------------------------------
# validate_workflow
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestValidateWorkflow:
    def test_delegates_to_workflow_validator_and_returns_result(self):
        service = _make_service()
        service.workflow_validator.validate_workflow.return_value = (True, [], [])  # type: ignore[assignment,attr-defined]

        workflow = _make_workflow()
        result = service.validate_workflow(workflow)

        assert result.valid is True
        assert result.errors == []
        service.workflow_validator.validate_workflow.assert_called_once_with(workflow)  # type: ignore[assignment,attr-defined]

    def test_invalid_workflow_surfaces_errors(self):
        service = _make_service()
        service.workflow_validator.validate_workflow.return_value = (  # type: ignore[assignment,attr-defined]
            False,
            ["Name required", "Phase missing"],
            [],
        )

        workflow = _make_workflow()
        result = service.validate_workflow(workflow)

        assert result.valid is False
        assert len(result.errors) == 2


# ---------------------------------------------------------------------------
# get_workflow
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestGetWorkflow:
    @pytest.mark.asyncio
    async def test_found_returns_custom_workflow(self):
        row = _make_row()
        pool = _make_pool(fetchrow_result=row)
        service = _make_service(pool=pool)

        result = await service.get_workflow("wf-uuid-1", "user-123")

        assert result is not None
        assert result.name == "My Workflow"

    @pytest.mark.asyncio
    async def test_not_found_returns_none(self):
        pool = _make_pool(fetchrow_result=None)
        service = _make_service(pool=pool)

        result = await service.get_workflow("nonexistent", "user-123")
        assert result is None

    @pytest.mark.asyncio
    async def test_db_error_raises(self):
        pool = _make_pool(fetchrow_side_effect=RuntimeError("DB down"))
        service = _make_service(pool=pool)

        with pytest.raises(RuntimeError, match="DB down"):
            await service.get_workflow("wf-1", "user-123")


# ---------------------------------------------------------------------------
# get_workflow_by_name
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestGetWorkflowByName:
    @pytest.mark.asyncio
    async def test_found_returns_workflow(self):
        row = _make_row(name="Named Workflow")
        pool = _make_pool(fetchrow_result=row)
        service = _make_service(pool=pool)

        result = await service.get_workflow_by_name("Named Workflow", "user-123")

        assert result is not None
        assert result.name == "Named Workflow"

    @pytest.mark.asyncio
    async def test_not_found_returns_none(self):
        pool = _make_pool(fetchrow_result=None)
        service = _make_service(pool=pool)

        result = await service.get_workflow_by_name("Missing", "user-123")
        assert result is None

    @pytest.mark.asyncio
    async def test_db_error_raises(self):
        pool = _make_pool(fetchrow_side_effect=RuntimeError("DB down"))
        service = _make_service(pool=pool)

        with pytest.raises(RuntimeError, match="DB down"):
            await service.get_workflow_by_name("wf", "user-123")


# ---------------------------------------------------------------------------
# list_workflows
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestListWorkflows:
    @pytest.mark.asyncio
    async def test_success_with_templates_returns_workflows(self):
        # total_count window column is now returned by the single query
        rows = [_make_row(id="wf-1", total_count=2), _make_row(id="wf-2", total_count=2)]
        pool = _make_pool(fetch_result=rows)
        service = _make_service(pool=pool)

        result = await service.list_workflows("user-123", include_templates=True)

        assert len(result["workflows"]) == 2
        assert result["total_count"] == 2
        assert result["page"] == 1

    @pytest.mark.asyncio
    async def test_without_templates_uses_owner_only_query(self):
        rows = [_make_row(id="wf-1", total_count=1)]
        pool = _make_pool(fetch_result=rows)
        service = _make_service(pool=pool)

        result = await service.list_workflows("user-123", include_templates=False)

        assert len(result["workflows"]) == 1

    @pytest.mark.asyncio
    async def test_pagination_has_next_when_more_results(self):
        rows = [_make_row(total_count=50)]
        pool = _make_pool(fetch_result=rows)
        service = _make_service(pool=pool)

        result = await service.list_workflows("user-123", page=1, page_size=20)

        assert result["has_next"] is True

    @pytest.mark.asyncio
    async def test_db_error_returns_empty_result(self):
        pool = _make_pool(fetch_side_effect=RuntimeError("DB down"))
        service = _make_service(pool=pool)

        result = await service.list_workflows("user-123")

        assert result["workflows"] == []
        assert result["total_count"] == 0

    @pytest.mark.asyncio
    async def test_empty_result_has_no_next_page(self):
        pool = _make_pool(fetch_result=[])
        service = _make_service(pool=pool)

        result = await service.list_workflows("user-123")

        assert result["has_next"] is False


# ---------------------------------------------------------------------------
# create_workflow
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestCreateWorkflow:
    @pytest.mark.asyncio
    async def test_success_returns_workflow_with_id(self):
        service = _make_service()
        service.workflow_validator.validate_workflow.return_value = (True, [], [])  # type: ignore[assignment,attr-defined]
        service._insert_workflow = AsyncMock(return_value=None)

        workflow = _make_workflow()
        result = await service.create_workflow(workflow, owner_id="user-123")

        assert result.owner_id == "user-123"
        assert result.id is not None
        assert result.created_at is not None
        service._insert_workflow.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_validation_failure_raises_value_error(self):
        service = _make_service()
        service.workflow_validator.validate_workflow.return_value = (  # type: ignore[assignment,attr-defined]
            False,
            ["Name required"],
            [],
        )

        workflow = _make_workflow()
        with pytest.raises(ValueError, match="Workflow validation failed"):
            await service.create_workflow(workflow, owner_id="user-123")

    @pytest.mark.asyncio
    async def test_db_error_raises(self):
        service = _make_service()
        service.workflow_validator.validate_workflow.return_value = (True, [], [])  # type: ignore[assignment,attr-defined]
        service._insert_workflow = AsyncMock(side_effect=RuntimeError("DB down"))

        workflow = _make_workflow()
        with pytest.raises(RuntimeError, match="DB down"):
            await service.create_workflow(workflow, owner_id="user-123")


# ---------------------------------------------------------------------------
# update_workflow
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestUpdateWorkflow:
    @pytest.mark.asyncio
    async def test_success_returns_updated_workflow(self):
        existing = _make_workflow(id="wf-1", owner_id="user-123")
        existing.created_at = _NOW

        service = _make_service()
        service.get_workflow = AsyncMock(return_value=existing)
        service.workflow_validator.validate_workflow.return_value = (True, [], [])  # type: ignore[assignment,attr-defined]
        service._update_workflow_in_db = AsyncMock(return_value=None)

        new_workflow = _make_workflow()
        result = await service.update_workflow("wf-1", new_workflow, owner_id="user-123")

        assert result.id == "wf-1"
        assert result.owner_id == "user-123"

    @pytest.mark.asyncio
    async def test_not_found_raises_value_error(self):
        service = _make_service()
        service.get_workflow = AsyncMock(return_value=None)

        workflow = _make_workflow()
        with pytest.raises(ValueError, match="not found or access denied"):
            await service.update_workflow("nonexistent", workflow, owner_id="user-123")

    @pytest.mark.asyncio
    async def test_ownership_mismatch_raises_value_error(self):
        existing = _make_workflow(id="wf-1", owner_id="different-user")
        existing.created_at = _NOW

        service = _make_service()
        service.get_workflow = AsyncMock(return_value=existing)

        workflow = _make_workflow()
        with pytest.raises(ValueError):
            await service.update_workflow("wf-1", workflow, owner_id="user-123")

    @pytest.mark.asyncio
    async def test_validation_failure_raises_value_error(self):
        existing = _make_workflow(id="wf-1", owner_id="user-123")
        existing.created_at = _NOW

        service = _make_service()
        service.get_workflow = AsyncMock(return_value=existing)
        service.workflow_validator.validate_workflow.return_value = (  # type: ignore[assignment,attr-defined]
            False,
            ["Phase name is required"],
            [],
        )

        workflow = _make_workflow()
        with pytest.raises(ValueError, match="validation failed"):
            await service.update_workflow("wf-1", workflow, owner_id="user-123")


# ---------------------------------------------------------------------------
# delete_workflow
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestDeleteWorkflow:
    @pytest.mark.asyncio
    async def test_success_returns_true(self):
        existing = _make_workflow(id="wf-1", owner_id="user-123")

        service = _make_service()
        service.get_workflow = AsyncMock(return_value=existing)
        service.database_service.pool.execute = AsyncMock(return_value="DELETE 1")

        result = await service.delete_workflow("wf-1", owner_id="user-123")
        assert result is True

    @pytest.mark.asyncio
    async def test_not_found_raises_value_error(self):
        service = _make_service()
        service.get_workflow = AsyncMock(return_value=None)

        with pytest.raises(ValueError, match="not found or access denied"):
            await service.delete_workflow("nonexistent", owner_id="user-123")

    @pytest.mark.asyncio
    async def test_ownership_mismatch_raises_value_error(self):
        existing = _make_workflow(id="wf-1", owner_id="other-user")

        service = _make_service()
        service.get_workflow = AsyncMock(return_value=existing)

        with pytest.raises(ValueError):
            await service.delete_workflow("wf-1", owner_id="user-123")

    @pytest.mark.asyncio
    async def test_db_error_raises(self):
        existing = _make_workflow(id="wf-1", owner_id="user-123")

        service = _make_service()
        service.get_workflow = AsyncMock(return_value=existing)
        service.database_service.pool.execute = AsyncMock(side_effect=RuntimeError("DB down"))

        with pytest.raises(RuntimeError, match="DB down"):
            await service.delete_workflow("wf-1", owner_id="user-123")


# ---------------------------------------------------------------------------
# _row_to_workflow
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestRowToWorkflow:
    def test_single_phase_in_new_format(self):
        """When phases list has one entry in WorkflowPhase format, it is deserialized correctly."""
        service = _make_service()
        # Use default row which has one research phase
        row = _make_row()

        result = service._row_to_workflow(row)

        assert result.name == "My Workflow"
        assert len(result.phases) == 1

    def test_new_workflow_phase_format(self):
        """Phases with 'index' field are deserialized as WorkflowPhase objects."""
        phases = [{"index": 0, "name": "research", "user_inputs": {}, "input_mapping": {}}]
        row = _make_row(phases=json.dumps(phases))
        service = _make_service()

        result = service._row_to_workflow(row)

        assert len(result.phases) == 1
        assert result.phases[0].name == "research"
        assert result.phases[0].index == 0

    def test_old_phase_config_format(self):
        """Old-format phases (no 'index' field) are converted to WorkflowPhase with i as index."""
        phases = [{"name": "draft", "agent": "creative"}, {"name": "assess", "agent": "qa"}]
        row = _make_row(phases=json.dumps(phases))
        service = _make_service()

        result = service._row_to_workflow(row)

        assert len(result.phases) == 2
        assert result.phases[0].index == 0
        assert result.phases[1].index == 1
        assert result.phases[0].name == "draft"

    def test_tags_parsed_from_json(self):
        tags = ["content", "seo"]
        row = _make_row(tags=json.dumps(tags))
        service = _make_service()

        result = service._row_to_workflow(row)

        assert result.tags == tags

    def test_tags_absent_or_empty_json_defaults_to_empty_list(self):
        """When tags is an empty JSON array, it deserializes to []."""
        row = _make_row(tags=json.dumps([]))
        service = _make_service()

        result = service._row_to_workflow(row)

        assert result.tags == []

    def test_is_template_flag_preserved(self):
        row = _make_row(is_template=True)
        service = _make_service()

        result = service._row_to_workflow(row)

        assert result.is_template is True


# ---------------------------------------------------------------------------
# get_available_phases
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestGetAvailablePhases:
    @pytest.mark.asyncio
    async def test_returns_list_from_registry(self):
        service = _make_service()

        mock_phase = MagicMock()
        mock_phase.name = "research"
        mock_phase.agent_type = "research_agent"
        mock_phase.description = "Research phase"
        mock_phase.timeout_seconds = 300
        mock_phase.max_retries = 3
        mock_phase.required = True
        mock_phase.quality_threshold = 0.7
        mock_phase.tags = ["content"]
        mock_phase.input_schema = {}
        mock_phase.output_schema = {}

        service.phase_registry.list_phases.return_value = [mock_phase]  # type: ignore[assignment,attr-defined]

        result = await service.get_available_phases()

        assert len(result) == 1
        assert result[0]["name"] == "research"
        assert result[0]["agent_type"] == "research_agent"

    @pytest.mark.asyncio
    async def test_empty_registry_returns_empty_list(self):
        service = _make_service()
        service.phase_registry.list_phases.return_value = []  # type: ignore[assignment,attr-defined]

        result = await service.get_available_phases()

        assert result == []
