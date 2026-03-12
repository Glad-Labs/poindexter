"""
Unit tests for PhaseRegistry and related dataclasses.

Tests singleton behaviour, built-in phase registration, InputField/OutputField
serialisation, PhaseDefinition metadata, and dynamic phase registration.
No LLM or DB calls.
"""

import pytest

from services.phase_registry import (
    ContentType,
    InputField,
    InputType,
    OutputField,
    PhaseDefinition,
    PhaseRegistry,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def reset_registry():
    """
    Reset the PhaseRegistry singleton between tests so registrations
    from one test don't leak into another.
    """
    PhaseRegistry._instance = None
    yield
    PhaseRegistry._instance = None


@pytest.fixture
def registry() -> PhaseRegistry:
    return PhaseRegistry.get_instance()


# ---------------------------------------------------------------------------
# Singleton
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestSingleton:
    def test_get_instance_returns_same_object(self):
        r1 = PhaseRegistry.get_instance()
        r2 = PhaseRegistry.get_instance()
        assert r1 is r2

    def test_direct_instantiation_returns_same_object(self):
        r1 = PhaseRegistry()
        r2 = PhaseRegistry()
        assert r1 is r2


# ---------------------------------------------------------------------------
# Built-in phases
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestBuiltinPhases:
    BUILTIN_PHASES = ["research", "draft", "assess", "refine", "image", "publish"]
    BLOG_PHASES = [
        "blog_generate_content",
        "blog_quality_evaluation",
        "blog_search_image",
        "blog_create_post",
    ]

    def test_all_builtin_phases_present(self, registry: PhaseRegistry):
        for name in self.BUILTIN_PHASES:
            assert registry.phase_exists(name), f"Built-in phase missing: {name}"

    def test_all_blog_phases_present(self, registry: PhaseRegistry):
        for name in self.BLOG_PHASES:
            assert registry.phase_exists(name), f"Blog phase missing: {name}"

    def test_total_phases_count(self, registry: PhaseRegistry):
        total = len(self.BUILTIN_PHASES) + len(self.BLOG_PHASES)
        assert len(registry.list_phases()) == total

    def test_research_phase_has_required_inputs(self, registry: PhaseRegistry):
        phase = registry.get_phase("research")
        assert phase is not None
        assert "topic" in phase.input_schema
        assert phase.input_schema["topic"].required is True

    def test_draft_phase_has_required_prompt_input(self, registry: PhaseRegistry):
        phase = registry.get_phase("draft")
        assert phase is not None
        assert "prompt" in phase.input_schema
        assert phase.input_schema["prompt"].required is True

    def test_assess_phase_has_quality_threshold_input(self, registry: PhaseRegistry):
        phase = registry.get_phase("assess")
        assert phase is not None
        assert "quality_threshold" in phase.input_schema

    def test_publish_phase_has_content_input(self, registry: PhaseRegistry):
        phase = registry.get_phase("publish")
        assert phase is not None
        assert "content" in phase.input_schema

    def test_blog_quality_phase_has_content_and_topic(self, registry: PhaseRegistry):
        phase = registry.get_phase("blog_quality_evaluation")
        assert phase is not None
        assert "content" in phase.input_schema
        assert "topic" in phase.input_schema

    def test_blog_search_image_has_topic_required(self, registry: PhaseRegistry):
        phase = registry.get_phase("blog_search_image")
        assert phase is not None
        assert "topic" in phase.input_schema
        assert phase.input_schema["topic"].required is True


# ---------------------------------------------------------------------------
# get_phase / phase_exists
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestGetPhase:
    def test_get_known_phase_returns_definition(self, registry: PhaseRegistry):
        phase = registry.get_phase("research")
        assert phase is not None
        assert isinstance(phase, PhaseDefinition)

    def test_get_unknown_phase_returns_none(self, registry: PhaseRegistry):
        result = registry.get_phase("nonexistent_phase")
        assert result is None

    def test_phase_exists_true_for_known(self, registry: PhaseRegistry):
        assert registry.phase_exists("draft") is True

    def test_phase_exists_false_for_unknown(self, registry: PhaseRegistry):
        assert registry.phase_exists("totally_missing") is False


# ---------------------------------------------------------------------------
# list_phases / list_phase_names
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestListPhases:
    def test_list_phases_returns_list_of_definitions(self, registry: PhaseRegistry):
        phases = registry.list_phases()
        assert isinstance(phases, list)
        assert all(isinstance(p, PhaseDefinition) for p in phases)

    def test_list_phase_names_returns_strings(self, registry: PhaseRegistry):
        names = registry.list_phase_names()
        assert isinstance(names, list)
        assert all(isinstance(n, str) for n in names)

    def test_list_phase_names_matches_list_phases(self, registry: PhaseRegistry):
        names = registry.list_phase_names()
        phases = registry.list_phases()
        assert set(names) == {p.name for p in phases}


# ---------------------------------------------------------------------------
# register_phase
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestRegisterPhase:
    def _make_phase(self, name: str) -> PhaseDefinition:
        return PhaseDefinition(
            name=name,
            agent_type="test_agent",
            description="A test phase",
            input_schema={
                "input_a": InputField(key="input_a", label="Input A", required=True),
            },
            output_schema={
                "output_a": OutputField(key="output_a", label="Output A"),
            },
        )

    def test_register_new_phase(self, registry: PhaseRegistry):
        phase = self._make_phase("custom_phase")
        registry.register_phase(phase)
        assert registry.phase_exists("custom_phase")
        assert registry.get_phase("custom_phase") is phase

    def test_register_overwrites_existing_phase(self, registry: PhaseRegistry):
        phase1 = self._make_phase("overwrite_test")
        phase2 = PhaseDefinition(
            name="overwrite_test",
            agent_type="updated_agent",
            description="Updated description",
        )
        registry.register_phase(phase1)
        registry.register_phase(phase2)  # Should overwrite
        retrieved = registry.get_phase("overwrite_test")
        assert retrieved is not None
        assert retrieved.agent_type == "updated_agent"

    def test_registered_phase_appears_in_list(self, registry: PhaseRegistry):
        initial_count = len(registry.list_phases())
        registry.register_phase(self._make_phase("extra_phase"))
        assert len(registry.list_phases()) == initial_count + 1


# ---------------------------------------------------------------------------
# PhaseDefinition.to_dict
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestPhaseDefinitionToDict:
    def test_to_dict_contains_required_fields(self, registry: PhaseRegistry):
        phase = registry.get_phase("research")
        assert phase is not None
        d = phase.to_dict()
        required = {"name", "agent_type", "description", "input_schema", "output_schema",
                    "required", "timeout_seconds", "max_retries", "skip_on_error", "tags"}
        assert required <= set(d.keys())

    def test_to_dict_name_matches(self, registry: PhaseRegistry):
        phase = registry.get_phase("draft")
        assert phase is not None
        assert phase.to_dict()["name"] == "draft"

    def test_to_dict_input_schema_is_dict(self, registry: PhaseRegistry):
        phase = registry.get_phase("assess")
        assert phase is not None
        d = phase.to_dict()
        assert isinstance(d["input_schema"], dict)

    def test_to_dict_output_schema_is_dict(self, registry: PhaseRegistry):
        phase = registry.get_phase("refine")
        assert phase is not None
        d = phase.to_dict()
        assert isinstance(d["output_schema"], dict)

    def test_to_dict_tags_is_list(self, registry: PhaseRegistry):
        phase = registry.get_phase("research")
        assert phase is not None
        assert isinstance(phase.to_dict()["tags"], list)


# ---------------------------------------------------------------------------
# InputField.to_dict
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestInputFieldToDict:
    def test_to_dict_contains_all_fields(self):
        field = InputField(
            key="my_key",
            label="My Label",
            input_type=InputType.TEXT,
            required=True,
            default_value="default",
            description="A description",
            placeholder="Enter value...",
        )
        d = field.to_dict()
        assert d["key"] == "my_key"
        assert d["label"] == "My Label"
        assert d["input_type"] == InputType.TEXT.value
        assert d["required"] is True
        assert d["default_value"] == "default"
        assert d["description"] == "A description"
        assert d["placeholder"] == "Enter value..."

    def test_to_dict_with_select_options(self):
        options = [{"label": "Option A", "value": "a"}, {"label": "Option B", "value": "b"}]
        field = InputField(
            key="select_key",
            label="Select Field",
            input_type=InputType.SELECT,
            options=options,
        )
        d = field.to_dict()
        assert d["options"] == options

    def test_input_type_enum_serialised_as_string(self):
        field = InputField(key="k", label="L", input_type=InputType.TEXTAREA)
        assert field.to_dict()["input_type"] == "textarea"

    def test_defaults_for_optional_fields(self):
        field = InputField(key="simple", label="Simple")
        d = field.to_dict()
        assert d["required"] is False
        assert d["default_value"] is None
        assert d["description"] is None
        assert d["placeholder"] is None
        assert d["options"] is None
        assert d["validation_pattern"] is None


# ---------------------------------------------------------------------------
# OutputField.to_dict
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestOutputFieldToDict:
    def test_to_dict_basic_fields(self):
        field = OutputField(
            key="out_key",
            label="Out Label",
            content_type=ContentType.JSON,
            description="Output description",
            example="{}",
        )
        d = field.to_dict()
        assert d["key"] == "out_key"
        assert d["label"] == "Out Label"
        assert d["content_type"] == ContentType.JSON.value
        assert d["description"] == "Output description"
        assert d["example"] == "{}"

    def test_content_type_enum_serialised_as_string(self):
        field = OutputField(key="k", label="L", content_type=ContentType.TEXT)
        assert field.to_dict()["content_type"] == "text"

    def test_defaults_for_optional_fields(self):
        field = OutputField(key="k", label="L")
        d = field.to_dict()
        assert d["description"] is None
        assert d["example"] is None


# ---------------------------------------------------------------------------
# ContentType / InputType enum coverage
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestEnumCoverage:
    def test_content_type_values(self):
        expected = {"text", "json", "object", "number", "boolean", "array"}
        actual = {c.value for c in ContentType}
        assert expected == actual

    def test_input_type_values(self):
        expected = {"text", "textarea", "number", "select", "boolean", "email"}
        actual = {t.value for t in InputType}
        assert expected == actual
