"""
Unit tests for PhaseMapper service.

Tests exact-key matching, semantic similarity matching, user override
handling, mapping validation, and the build_full_phase_pipeline helper.
No LLM or DB calls.
"""

import pytest

from services.phase_mapper import PhaseMapper, PhaseMappingError, build_full_phase_pipeline
from services.phase_registry import (
    InputField,
    OutputField,
    PhaseDefinition,
    PhaseRegistry,
)

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def reset_registry():
    """Reset the PhaseRegistry singleton between tests."""
    PhaseRegistry._instance = None
    yield
    PhaseRegistry._instance = None


@pytest.fixture
def registry() -> PhaseRegistry:
    """Return a fresh registry with a set of test phases."""
    reg = PhaseRegistry.get_instance()
    return reg


@pytest.fixture
def mapper(registry) -> PhaseMapper:
    return PhaseMapper(registry=registry)


def _make_phase(name: str, inputs: dict, outputs: dict, **kwargs) -> PhaseDefinition:
    """Helper to build a PhaseDefinition with custom schema."""
    return PhaseDefinition(
        name=name,
        agent_type="test_agent",
        description=f"Test phase: {name}",
        input_schema=inputs,
        output_schema=outputs,
        **kwargs,
    )


def _input(key: str, label: str, required: bool = False) -> InputField:
    return InputField(key=key, label=label, required=required)


def _output(key: str, label: str) -> OutputField:
    return OutputField(key=key, label=label)


# ---------------------------------------------------------------------------
# PhaseMapper.map_phases — exact key match
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestMapPhasesExactMatch:
    def test_exact_key_match(self, registry: PhaseRegistry, mapper: PhaseMapper):
        """A shared key between source output and target input is mapped directly."""
        registry.register_phase(
            _make_phase(
                "phase_a",
                inputs={},
                outputs={"content": _output("content", "Content")},
            )
        )
        registry.register_phase(
            _make_phase(
                "phase_b",
                inputs={"content": _input("content", "Content")},
                outputs={},
            )
        )
        mapping = mapper.map_phases("phase_a", "phase_b")
        assert mapping.get("content") == "content"

    def test_multiple_exact_matches(self, registry: PhaseRegistry, mapper: PhaseMapper):
        registry.register_phase(
            _make_phase(
                "src",
                inputs={},
                outputs={
                    "topic": _output("topic", "Topic"),
                    "draft": _output("draft", "Draft"),
                },
            )
        )
        registry.register_phase(
            _make_phase(
                "tgt",
                inputs={
                    "topic": _input("topic", "Topic"),
                    "draft": _input("draft", "Draft"),
                },
                outputs={},
            )
        )
        mapping = mapper.map_phases("src", "tgt")
        assert mapping.get("topic") == "topic"
        assert mapping.get("draft") == "draft"


# ---------------------------------------------------------------------------
# PhaseMapper.map_phases — user overrides
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestMapPhasesUserOverrides:
    def test_user_override_takes_precedence(self, registry: PhaseRegistry, mapper: PhaseMapper):
        registry.register_phase(
            _make_phase(
                "src2",
                inputs={},
                outputs={"findings": _output("findings", "Findings")},
            )
        )
        registry.register_phase(
            _make_phase(
                "tgt2",
                inputs={"content": _input("content", "Content")},
                outputs={},
            )
        )
        # User manually maps tgt2.content <- src2.findings
        mapping = mapper.map_phases("src2", "tgt2", user_overrides={"content": "findings"})
        assert mapping.get("content") == "findings"

    def test_user_override_skips_auto_match(self, registry: PhaseRegistry, mapper: PhaseMapper):
        """Keys already in user_overrides must not be re-mapped by auto logic."""
        registry.register_phase(
            _make_phase(
                "src3",
                inputs={},
                outputs={"content": _output("content", "Content")},
            )
        )
        registry.register_phase(
            _make_phase(
                "tgt3",
                inputs={"content": _input("content", "Content")},
                outputs={},
            )
        )
        # User provides a different mapping — auto-match should NOT overwrite it
        mapping = mapper.map_phases("src3", "tgt3", user_overrides={"content": "custom_source"})
        assert mapping["content"] == "custom_source"


# ---------------------------------------------------------------------------
# PhaseMapper.map_phases — error cases
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestMapPhasesErrors:
    def test_unknown_source_raises_mapping_error(
        self, registry: PhaseRegistry, mapper: PhaseMapper
    ):
        with pytest.raises(PhaseMappingError) as exc_info:
            mapper.map_phases("nonexistent_source", "research")
        assert "nonexistent_source" in str(exc_info.value)

    def test_unknown_target_raises_mapping_error(
        self, registry: PhaseRegistry, mapper: PhaseMapper
    ):
        with pytest.raises(PhaseMappingError) as exc_info:
            mapper.map_phases("research", "nonexistent_target")
        assert "nonexistent_target" in str(exc_info.value)

    def test_both_unknown_raises_mapping_error(self, registry: PhaseRegistry, mapper: PhaseMapper):
        with pytest.raises(PhaseMappingError):
            mapper.map_phases("ghost_src", "ghost_tgt")


# ---------------------------------------------------------------------------
# PhaseMapper._string_similarity
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestStringSimilarity:
    def test_identical_strings_score_1(self, mapper: PhaseMapper):
        assert mapper._string_similarity("content", "content") == 1.0

    def test_empty_strings_score_1(self, mapper: PhaseMapper):
        assert mapper._string_similarity("", "") == 1.0

    def test_completely_different_score_less_than_1(self, mapper: PhaseMapper):
        score = mapper._string_similarity("content", "xyz")
        assert score < 1.0

    def test_similar_strings_score_above_zero(self, mapper: PhaseMapper):
        score = mapper._string_similarity("content", "contents")
        assert score > 0.5

    def test_score_is_float(self, mapper: PhaseMapper):
        score = mapper._string_similarity("alpha", "beta")
        assert isinstance(score, float)

    def test_score_between_0_and_1(self, mapper: PhaseMapper):
        for a, b in [("foo", "bar"), ("hello", "world"), ("abc", "abc")]:
            score = mapper._string_similarity(a, b)
            assert 0.0 <= score <= 1.0


# ---------------------------------------------------------------------------
# PhaseMapper.validate_mapping
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestValidateMapping:
    def _make_source(self) -> PhaseDefinition:
        return _make_phase(
            "src_v",
            inputs={},
            outputs={
                "content": _output("content", "Content"),
                "findings": _output("findings", "Findings"),
            },
        )

    def _make_target(self) -> PhaseDefinition:
        return _make_phase(
            "tgt_v",
            inputs={
                "content": _input("content", "Content", required=True),
                "topic": _input("topic", "Topic", required=False),
            },
            outputs={},
        )

    def test_valid_mapping_returns_true(self, mapper: PhaseMapper):
        src = self._make_source()
        tgt = self._make_target()
        mapping = {"content": "content"}
        is_valid, issues = mapper.validate_mapping(src, tgt, mapping)
        assert is_valid is True
        assert issues == []

    def test_invalid_target_key_caught(self, mapper: PhaseMapper):
        src = self._make_source()
        tgt = self._make_target()
        mapping = {"nonexistent_input": "content"}  # target key doesn't exist
        is_valid, issues = mapper.validate_mapping(src, tgt, mapping)
        assert is_valid is False
        assert any("nonexistent_input" in issue for issue in issues)

    def test_invalid_source_key_caught(self, mapper: PhaseMapper):
        src = self._make_source()
        tgt = self._make_target()
        mapping = {"content": "does_not_exist_in_src"}
        is_valid, issues = mapper.validate_mapping(src, tgt, mapping)
        assert is_valid is False
        assert any("does_not_exist_in_src" in issue for issue in issues)

    def test_missing_required_input_caught(self, mapper: PhaseMapper):
        src = self._make_source()
        tgt = self._make_target()
        mapping = {}  # content is required but not mapped
        is_valid, issues = mapper.validate_mapping(src, tgt, mapping)
        assert is_valid is False
        assert any("content" in issue for issue in issues)

    def test_empty_mapping_with_no_required_inputs_is_valid(self, mapper: PhaseMapper):
        src = _make_phase("src_no_req", inputs={}, outputs={"x": _output("x", "X")})
        tgt = _make_phase("tgt_no_req", inputs={"y": _input("y", "Y", required=False)}, outputs={})
        is_valid, issues = mapper.validate_mapping(src, tgt, {})
        assert is_valid is True
        assert issues == []


# ---------------------------------------------------------------------------
# build_full_phase_pipeline
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestBuildFullPhasePipeline:
    def test_single_phase_returns_empty_mapping(self, registry: PhaseRegistry):
        """Single phase has no previous phase to map from."""
        result = build_full_phase_pipeline(["research"])
        assert result == {}

    def test_two_phases_creates_one_mapping(self, registry: PhaseRegistry):
        """research -> draft creates a mapping for 'draft'."""
        result = build_full_phase_pipeline(["research", "draft"])
        assert "draft" in result
        assert isinstance(result["draft"], dict)

    def test_three_phases_creates_two_mappings(self, registry: PhaseRegistry):
        result = build_full_phase_pipeline(["research", "draft", "assess"])
        assert "draft" in result
        assert "assess" in result

    def test_empty_phase_list_raises(self):
        with pytest.raises(PhaseMappingError):
            build_full_phase_pipeline([])

    def test_user_mapping_override_applied(self, registry: PhaseRegistry):
        user_mappings = {"draft": {"prompt": "findings"}}
        result = build_full_phase_pipeline(["research", "draft"], user_mappings=user_mappings)
        assert result["draft"].get("prompt") == "findings"

    def test_unknown_phase_in_list_raises(self, registry: PhaseRegistry):
        with pytest.raises(PhaseMappingError):
            build_full_phase_pipeline(["research", "ghost_phase"])

    def test_research_draft_assess_refine_pipeline(self, registry: PhaseRegistry):
        result = build_full_phase_pipeline(["research", "draft", "assess", "refine"])
        assert set(result.keys()) == {"draft", "assess", "refine"}


# ---------------------------------------------------------------------------
# PhaseMappingError
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestPhaseMappingError:
    def test_is_exception(self):
        err = PhaseMappingError("test error")
        assert isinstance(err, Exception)

    def test_message_preserved(self):
        err = PhaseMappingError("specific message")
        assert "specific message" in str(err)
