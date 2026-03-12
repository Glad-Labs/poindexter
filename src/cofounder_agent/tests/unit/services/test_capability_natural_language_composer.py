"""
Unit tests for services/capability_natural_language_composer.py

Tests CapabilityNaturalLanguageComposer: prompt building, LLM response parsing,
task validation, dict-to-definition conversion, compose_from_request end-to-end,
and module-level factory.
"""

import json
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from services.capability_natural_language_composer import (
    CapabilityNaturalLanguageComposer,
    TaskCompositionResult,
    get_composer,
)
from services.capability_registry import CapabilityRegistry, set_registry
from services.capability_task_executor import CapabilityStep, CapabilityTaskDefinition


# ---------------------------------------------------------------------------
# Helpers / Fixtures
# ---------------------------------------------------------------------------


def _make_registry_with_caps(*names: str) -> CapabilityRegistry:
    """Build a CapabilityRegistry populated with minimal stub capabilities."""
    from services.capability_registry import (
        Capability,
        CapabilityMetadata,
        InputSchema,
    )
    reg = CapabilityRegistry()
    for name in names:
        meta = CapabilityMetadata(
            name=name, description=f"Stub {name}", tags=["test"], cost_tier="cheap"
        )
        schema = InputSchema(parameters=[])
        cap = MagicMock(spec=Capability)
        cap.metadata = meta
        cap.input_schema = schema
        reg._capabilities[name] = cap
        reg._metadata[name] = meta
    return reg


SAMPLE_TASK_DICT = {
    "name": "Research and publish AI post",
    "description": "Research AI and publish result",
    "steps": [
        {
            "capability_name": "research",
            "inputs": {"topic": "Artificial Intelligence"},
            "output_key": "research_output",
            "order": 0,
        },
        {
            "capability_name": "publish",
            "inputs": {"content": "$research_output"},
            "output_key": "publish_output",
            "order": 1,
        },
    ],
}


@pytest.fixture(autouse=True)
def isolated_registry():
    """Reset global registry and composer before each test."""
    import services.capability_natural_language_composer as mod
    mod._composer_instance = None
    reg = _make_registry_with_caps("research", "generate_content", "publish", "critique")
    set_registry(reg)
    yield
    mod._composer_instance = None


def make_composer() -> CapabilityNaturalLanguageComposer:
    mock_router = MagicMock()
    return CapabilityNaturalLanguageComposer(model_router=mock_router)


# ---------------------------------------------------------------------------
# Initialization
# ---------------------------------------------------------------------------


class TestCapabilityNaturalLanguageComposerInit:
    def test_registry_assigned(self):
        composer = make_composer()
        assert composer.registry is not None

    def test_model_router_assigned(self):
        mock_router = MagicMock()
        composer = CapabilityNaturalLanguageComposer(model_router=mock_router)
        assert composer.model_router is mock_router


# ---------------------------------------------------------------------------
# _get_registry_context
# ---------------------------------------------------------------------------


class TestGetRegistryContext:
    def test_contains_capability_names(self):
        composer = make_composer()
        context = composer._get_registry_context()
        assert "research" in context
        assert "publish" in context

    def test_contains_available_capabilities_header(self):
        composer = make_composer()
        context = composer._get_registry_context()
        assert "Available Capabilities" in context


# ---------------------------------------------------------------------------
# _create_composition_prompt
# ---------------------------------------------------------------------------


class TestCreateCompositionPrompt:
    def test_includes_user_request(self):
        composer = make_composer()
        prompt = composer._create_composition_prompt("Write a blog post about AI")
        assert "Write a blog post about AI" in prompt

    def test_includes_json_format_instructions(self):
        composer = make_composer()
        prompt = composer._create_composition_prompt("test request")
        assert "JSON" in prompt
        assert "steps" in prompt

    def test_includes_registry_context(self):
        composer = make_composer()
        prompt = composer._create_composition_prompt("test")
        assert "research" in prompt


# ---------------------------------------------------------------------------
# _parse_llm_response
# ---------------------------------------------------------------------------


class TestParseLLMResponse:
    def test_parses_valid_json(self):
        composer = make_composer()
        response = json.dumps({"name": "test", "steps": []})
        result = composer._parse_llm_response(response)
        assert result["name"] == "test"

    def test_extracts_json_from_surrounding_text(self):
        composer = make_composer()
        response = 'Here is the JSON:\n{"name": "task", "steps": []}\nDone!'
        result = composer._parse_llm_response(response)
        assert result["name"] == "task"

    def test_no_json_returns_error_key(self):
        composer = make_composer()
        result = composer._parse_llm_response("no json here at all")
        assert "error" in result

    def test_invalid_json_returns_error_key(self):
        composer = make_composer()
        result = composer._parse_llm_response("{invalid json }")
        assert "error" in result

    def test_error_key_in_response_preserved(self):
        composer = make_composer()
        response = json.dumps({"error": "cannot fulfill request"})
        result = composer._parse_llm_response(response)
        assert result["error"] == "cannot fulfill request"


# ---------------------------------------------------------------------------
# _validate_task_definition
# ---------------------------------------------------------------------------


class TestValidateTaskDefinition:
    def test_valid_task_passes(self):
        composer = make_composer()
        result = composer._validate_task_definition(SAMPLE_TASK_DICT)
        assert result["valid"] is True

    def test_missing_name_fails(self):
        composer = make_composer()
        bad = {k: v for k, v in SAMPLE_TASK_DICT.items() if k != "name"}
        result = composer._validate_task_definition(bad)
        assert result["valid"] is False
        assert "name" in result["error"].lower()

    def test_missing_steps_fails(self):
        composer = make_composer()
        bad = {"name": "test"}
        result = composer._validate_task_definition(bad)
        assert result["valid"] is False
        assert "steps" in result["error"].lower()

    def test_empty_steps_fails(self):
        composer = make_composer()
        bad = {"name": "test", "steps": []}
        result = composer._validate_task_definition(bad)
        assert result["valid"] is False

    def test_unknown_capability_fails(self):
        composer = make_composer()
        bad = {
            "name": "test",
            "steps": [
                {
                    "capability_name": "unknown_cap",
                    "inputs": {},
                    "output_key": "out",
                }
            ],
        }
        result = composer._validate_task_definition(bad)
        assert result["valid"] is False
        assert "unknown_cap" in result["error"]

    def test_valid_task_confidence_positive(self):
        composer = make_composer()
        result = composer._validate_task_definition(SAMPLE_TASK_DICT)
        assert result["confidence"] > 0

    def test_missing_output_key_fails(self):
        composer = make_composer()
        bad = {
            "name": "test",
            "steps": [
                {"capability_name": "research", "inputs": {}}
                # missing output_key
            ],
        }
        result = composer._validate_task_definition(bad)
        assert result["valid"] is False


# ---------------------------------------------------------------------------
# _dict_to_task_definition
# ---------------------------------------------------------------------------


class TestDictToTaskDefinition:
    def test_returns_capability_task_definition(self):
        composer = make_composer()
        task_def = composer._dict_to_task_definition(SAMPLE_TASK_DICT)
        assert isinstance(task_def, CapabilityTaskDefinition)

    def test_name_preserved(self):
        composer = make_composer()
        task_def = composer._dict_to_task_definition(SAMPLE_TASK_DICT)
        assert task_def.name == SAMPLE_TASK_DICT["name"]

    def test_steps_count_matches(self):
        composer = make_composer()
        task_def = composer._dict_to_task_definition(SAMPLE_TASK_DICT)
        assert len(task_def.steps) == 2

    def test_step_capability_names(self):
        composer = make_composer()
        task_def = composer._dict_to_task_definition(SAMPLE_TASK_DICT)
        names = [s.capability_name for s in task_def.steps]
        assert names == ["research", "publish"]

    def test_step_order_assigned(self):
        composer = make_composer()
        task_def = composer._dict_to_task_definition(SAMPLE_TASK_DICT)
        orders = [s.order for s in task_def.steps]
        assert orders == [0, 1]

    def test_step_inputs_preserved(self):
        composer = make_composer()
        task_def = composer._dict_to_task_definition(SAMPLE_TASK_DICT)
        assert task_def.steps[0].inputs == {"topic": "Artificial Intelligence"}

    def test_missing_description_defaults_empty(self):
        composer = make_composer()
        task_dict = {"name": "test", "steps": []}
        task_def = composer._dict_to_task_definition(task_dict)
        assert task_def.description == ""


# ---------------------------------------------------------------------------
# compose_from_request — happy path
# ---------------------------------------------------------------------------


class TestComposeFromRequest:
    @pytest.mark.asyncio
    async def test_successful_composition(self):
        composer = make_composer()
        llm_response = json.dumps(SAMPLE_TASK_DICT)
        with patch.object(composer, "_call_llm", new=AsyncMock(return_value=llm_response)):
            result = await composer.compose_from_request("Research and publish AI")
        assert isinstance(result, TaskCompositionResult)
        assert result.success is True
        assert result.task_definition is not None

    @pytest.mark.asyncio
    async def test_suggested_task_populated(self):
        composer = make_composer()
        llm_response = json.dumps(SAMPLE_TASK_DICT)
        with patch.object(composer, "_call_llm", new=AsyncMock(return_value=llm_response)):
            result = await composer.compose_from_request("Research AI")
        assert result.suggested_task is not None
        assert result.suggested_task["name"] == SAMPLE_TASK_DICT["name"]

    @pytest.mark.asyncio
    async def test_explanation_contains_capability_names(self):
        composer = make_composer()
        llm_response = json.dumps(SAMPLE_TASK_DICT)
        with patch.object(composer, "_call_llm", new=AsyncMock(return_value=llm_response)):
            result = await composer.compose_from_request("test")
        assert "research" in result.explanation
        assert "publish" in result.explanation

    @pytest.mark.asyncio
    async def test_confidence_set(self):
        composer = make_composer()
        llm_response = json.dumps(SAMPLE_TASK_DICT)
        with patch.object(composer, "_call_llm", new=AsyncMock(return_value=llm_response)):
            result = await composer.compose_from_request("test")
        assert result.confidence > 0

    @pytest.mark.asyncio
    async def test_llm_error_key_returns_failure(self):
        composer = make_composer()
        llm_response = json.dumps({"error": "Cannot fulfill this request"})
        with patch.object(composer, "_call_llm", new=AsyncMock(return_value=llm_response)):
            result = await composer.compose_from_request("impossible task")
        assert result.success is False
        assert result.error is not None

    @pytest.mark.asyncio
    async def test_invalid_json_from_llm_returns_failure(self):
        composer = make_composer()
        with patch.object(composer, "_call_llm", new=AsyncMock(return_value="not json")):
            result = await composer.compose_from_request("test")
        assert result.success is False

    @pytest.mark.asyncio
    async def test_llm_exception_returns_failure(self):
        composer = make_composer()
        with patch.object(
            composer, "_call_llm", new=AsyncMock(side_effect=RuntimeError("LLM down"))
        ):
            result = await composer.compose_from_request("test")
        assert result.success is False
        assert result.error is not None


# ---------------------------------------------------------------------------
# compose_from_request — auto_execute
# ---------------------------------------------------------------------------


class TestComposeFromRequestAutoExecute:
    @pytest.mark.asyncio
    async def test_auto_execute_calls_execute(self):
        composer = make_composer()
        llm_response = json.dumps(SAMPLE_TASK_DICT)
        mock_exec_result = MagicMock()
        mock_exec_result.execution_id = "exec-abc-123"

        with patch.object(composer, "_call_llm", new=AsyncMock(return_value=llm_response)):
            with patch(
                "services.capability_natural_language_composer.execute_capability_task",
                new=AsyncMock(return_value=mock_exec_result),
            ):
                result = await composer.compose_from_request("test", auto_execute=True)

        assert result.success is True
        assert result.execution_id == "exec-abc-123"

    @pytest.mark.asyncio
    async def test_execution_failure_returns_failure_with_suggested_task(self):
        composer = make_composer()
        llm_response = json.dumps(SAMPLE_TASK_DICT)

        with patch.object(composer, "_call_llm", new=AsyncMock(return_value=llm_response)):
            with patch(
                "services.capability_natural_language_composer.execute_capability_task",
                new=AsyncMock(side_effect=RuntimeError("Executor down")),
            ):
                result = await composer.compose_from_request("test", auto_execute=True)

        assert result.success is False
        assert result.suggested_task is not None


# ---------------------------------------------------------------------------
# get_composer factory
# ---------------------------------------------------------------------------


class TestGetComposer:
    def test_returns_instance(self):
        import services.capability_natural_language_composer as mod
        mod._composer_instance = None
        composer = get_composer()
        assert isinstance(composer, CapabilityNaturalLanguageComposer)

    def test_returns_same_instance_on_repeat(self):
        import services.capability_natural_language_composer as mod
        mod._composer_instance = None
        c1 = get_composer()
        c2 = get_composer()
        assert c1 is c2
