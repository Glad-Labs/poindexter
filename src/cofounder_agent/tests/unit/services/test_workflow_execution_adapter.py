"""
Unit tests for services/workflow_execution_adapter.py.

Tests cover the pure utility functions (no I/O, no LLM calls):
- _normalize_phase_alias — casing, underscores, hyphens, trailing digits, non-string
- _is_resolvable_agent_name — ends with _agent vs not
- resolve_phase_agent_name — direct agent name, PHASE_TO_AGENT_MAP lookup,
  metadata.phase_type lookup, phase_name fallback, fallback to creative_agent
- _json_default_serializer — datetime, Enum, model_dump, to_dict, str fallback
- _to_json_safe — None, primitives, dict, datetime, non-serializable object
- _is_content_phase_for_fallback — known phase names, metadata override, unknown
- _build_content_fallback_prompt — contains phase name, instruction, input data
- _extract_text_from_output — string, dict with known keys, nested dict, list, other
- _build_content_phase_fallback_result — per-phase fields (research, draft, assess,
  image, image_selection, publish, finalize, unknown)

No async calls, no mocking.
"""

import json
import pytest
from datetime import datetime, timezone
from enum import Enum

from services.workflow_execution_adapter import (
    PHASE_TO_AGENT_MAP,
    _build_content_fallback_prompt,
    _build_content_phase_fallback_result,
    _extract_text_from_output,
    _is_content_phase_for_fallback,
    _is_resolvable_agent_name,
    _json_default_serializer,
    _normalize_phase_alias,
    _to_json_safe,
    resolve_phase_agent_name,
)


# ---------------------------------------------------------------------------
# _normalize_phase_alias
# ---------------------------------------------------------------------------


class TestNormalizePhaseAlias:
    def test_lowercase(self):
        assert _normalize_phase_alias("Research") == "research"

    def test_strips_whitespace(self):
        assert _normalize_phase_alias("  draft  ") == "draft"

    def test_hyphens_to_underscores(self):
        assert _normalize_phase_alias("image-selection") == "image_selection"

    def test_spaces_to_underscores(self):
        assert _normalize_phase_alias("image selection") == "image_selection"

    def test_trailing_digits_removed(self):
        assert _normalize_phase_alias("research_2") == "research"
        assert _normalize_phase_alias("draft_10") == "draft"

    def test_non_string_returns_empty(self):
        assert _normalize_phase_alias(None) == ""
        assert _normalize_phase_alias(42) == ""
        assert _normalize_phase_alias([]) == ""

    def test_already_normalized(self):
        assert _normalize_phase_alias("publish") == "publish"


# ---------------------------------------------------------------------------
# _is_resolvable_agent_name
# ---------------------------------------------------------------------------


class TestIsResolvableAgentName:
    def test_ends_with_agent_suffix(self):
        assert _is_resolvable_agent_name("creative_agent") is True
        assert _is_resolvable_agent_name("research_agent") is True

    def test_does_not_end_with_agent_suffix(self):
        assert _is_resolvable_agent_name("draft") is False
        assert _is_resolvable_agent_name("creative") is False

    def test_empty_string(self):
        assert _is_resolvable_agent_name("") is False

    def test_just_agent_word(self):
        # "agent" does NOT end with "_agent" (requires underscore prefix)
        assert _is_resolvable_agent_name("agent") is False
        # "_agent" ends with "_agent"
        assert _is_resolvable_agent_name("_agent") is True


# ---------------------------------------------------------------------------
# resolve_phase_agent_name
# ---------------------------------------------------------------------------


class TestResolvePhaseAgentName:
    def test_configured_agent_already_concrete(self):
        """If configured_agent ends with _agent, return it directly."""
        result = resolve_phase_agent_name("publishing_agent")
        assert result == "publishing_agent"

    def test_maps_research_phase(self):
        result = resolve_phase_agent_name(None, phase_name="research")
        assert result == "research_agent"

    def test_maps_draft_phase(self):
        result = resolve_phase_agent_name(None, phase_name="draft")
        assert result == "creative_agent"

    def test_maps_assess_phase(self):
        result = resolve_phase_agent_name(None, phase_name="assess")
        assert result == "qa_agent"

    def test_maps_image_phase(self):
        result = resolve_phase_agent_name(None, phase_name="image")
        assert result == "image_agent"

    def test_maps_image_selection_phase(self):
        result = resolve_phase_agent_name(None, phase_name="image_selection")
        assert result == "image_agent"

    def test_metadata_phase_type_overrides_when_configured_agent_unrecognized(self):
        result = resolve_phase_agent_name(
            None,
            phase_metadata={"phase_type": "research"},
            phase_name=None,
        )
        assert result == "research_agent"

    def test_fallback_to_creative_agent_for_unknown(self):
        result = resolve_phase_agent_name(None, phase_name=None)
        assert result == "creative_agent"

    def test_hyphenated_phase_name_normalized(self):
        result = resolve_phase_agent_name(None, phase_name="image-selection")
        assert result == "image_agent"

    def test_all_known_phase_names_resolve(self):
        """Every phase in PHASE_TO_AGENT_MAP should resolve via phase_name."""
        for phase, expected_agent in PHASE_TO_AGENT_MAP.items():
            result = resolve_phase_agent_name(None, phase_name=phase)
            assert result == expected_agent, f"Phase '{phase}' expected '{expected_agent}', got '{result}'"


# ---------------------------------------------------------------------------
# _json_default_serializer
# ---------------------------------------------------------------------------


class _SampleEnum(Enum):
    VALUE_A = "a"
    VALUE_B = "b"


class _ModelDumpable:
    def model_dump(self):
        return {"key": "val"}


class _ToDictable:
    def to_dict(self):
        return {"dict_key": "dict_val"}


class TestJsonDefaultSerializer:
    def test_datetime_returns_isoformat(self):
        now = datetime(2026, 3, 12, 10, 0, 0, tzinfo=timezone.utc)
        assert _json_default_serializer(now) == now.isoformat()

    def test_enum_returns_value(self):
        assert _json_default_serializer(_SampleEnum.VALUE_A) == "a"

    def test_model_dump_called(self):
        obj = _ModelDumpable()
        result = _json_default_serializer(obj)
        assert result == {"key": "val"}

    def test_to_dict_called_when_no_model_dump(self):
        obj = _ToDictable()
        result = _json_default_serializer(obj)
        assert result == {"dict_key": "dict_val"}

    def test_str_fallback(self):
        result = _json_default_serializer(object())
        assert isinstance(result, str)


# ---------------------------------------------------------------------------
# _to_json_safe
# ---------------------------------------------------------------------------


class TestToJsonSafe:
    def test_none_returns_none(self):
        assert _to_json_safe(None) is None

    def test_primitive_string(self):
        assert _to_json_safe("hello") == "hello"

    def test_primitive_number(self):
        assert _to_json_safe(42) == 42

    def test_dict_passthrough(self):
        d = {"key": "val", "num": 1}
        assert _to_json_safe(d) == d

    def test_datetime_converted(self):
        now = datetime(2026, 3, 12, tzinfo=timezone.utc)
        result = _to_json_safe({"ts": now})
        assert isinstance(result["ts"], str)
        assert "2026" in result["ts"]

    def test_non_serializable_returns_string(self):
        class Unserializable:
            pass

        result = _to_json_safe(Unserializable())
        assert isinstance(result, str)


# ---------------------------------------------------------------------------
# _is_content_phase_for_fallback
# ---------------------------------------------------------------------------


class TestIsContentPhaseForFallback:
    @pytest.mark.parametrize("phase", [
        "research", "draft", "assess", "refine",
        "image", "image_selection", "publish", "finalize",
    ])
    def test_known_phases_return_true(self, phase):
        assert _is_content_phase_for_fallback(phase) is True

    def test_unknown_phase_returns_false(self):
        assert _is_content_phase_for_fallback("unknown_phase") is False

    def test_none_returns_false(self):
        assert _is_content_phase_for_fallback(None) is False

    def test_metadata_phase_type_overrides(self):
        assert _is_content_phase_for_fallback(
            "custom_phase", phase_metadata={"phase_type": "draft"}
        ) is True

    def test_metadata_unknown_phase_type_returns_false(self):
        assert _is_content_phase_for_fallback(
            "custom_phase", phase_metadata={"phase_type": "unknown_type"}
        ) is False


# ---------------------------------------------------------------------------
# _build_content_fallback_prompt
# ---------------------------------------------------------------------------


class TestBuildContentFallbackPrompt:
    def test_prompt_contains_phase_name(self):
        prompt = _build_content_fallback_prompt("research", {"topic": "AI"})
        assert "research" in prompt

    def test_prompt_contains_phase_instruction(self):
        prompt = _build_content_fallback_prompt("draft", {"topic": "AI"})
        assert "first draft" in prompt.lower() or "draft" in prompt.lower()

    def test_prompt_contains_input_data(self):
        prompt = _build_content_fallback_prompt("research", {"topic": "blockchain"})
        assert "blockchain" in prompt

    def test_prompt_for_unknown_phase_uses_fallback_instruction(self):
        prompt = _build_content_fallback_prompt("custom_phase", {})
        assert "custom_phase" in prompt

    def test_all_known_phases_have_instruction(self):
        for phase in ("research", "draft", "assess", "refine", "image",
                      "image_selection", "publish", "finalize"):
            prompt = _build_content_fallback_prompt(phase, {})
            assert len(prompt) > 0
            assert phase in prompt


# ---------------------------------------------------------------------------
# _extract_text_from_output
# ---------------------------------------------------------------------------


class TestExtractTextFromOutput:
    def test_string_returned_directly(self):
        assert _extract_text_from_output("plain text") == "plain text"

    def test_dict_with_output_key(self):
        result = _extract_text_from_output({"output": "extracted output"})
        assert result == "extracted output"

    def test_dict_with_content_key(self):
        result = _extract_text_from_output({"content": "blog post content"})
        assert result == "blog post content"

    def test_dict_with_research_findings_key(self):
        result = _extract_text_from_output({"research_findings": "findings"})
        assert result == "findings"

    def test_dict_with_nested_summary(self):
        result = _extract_text_from_output({"assessment": {"summary": "summary text"}})
        assert result == "summary text"

    def test_dict_with_no_known_key_returns_json(self):
        result = _extract_text_from_output({"unknown_key": "val"})
        assert "unknown_key" in result

    def test_list_returns_json(self):
        result = _extract_text_from_output(["item1", "item2"])
        assert "item1" in result

    def test_none_returns_string(self):
        result = _extract_text_from_output(None)
        assert isinstance(result, str)

    def test_integer_returns_string(self):
        result = _extract_text_from_output(42)
        assert isinstance(result, str)


# ---------------------------------------------------------------------------
# _build_content_phase_fallback_result
# ---------------------------------------------------------------------------


class TestBuildContentPhaseFallbackResult:
    def test_common_fields_always_present(self):
        result = _build_content_phase_fallback_result("draft", "text", "src", "reason")
        assert result["phase"] == "draft"
        assert result["output"] == "text"
        assert result["fallback_source"] == "src"
        assert result["fallback_reason"] == "reason"
        assert "timestamp" in result

    def test_research_phase_adds_research_findings(self):
        result = _build_content_phase_fallback_result("research", "findings", "s", "r")
        assert result["research_findings"] == "findings"

    def test_draft_phase_adds_content_and_draft_content(self):
        result = _build_content_phase_fallback_result("draft", "draft text", "s", "r")
        assert result["content"] == "draft text"
        assert result["draft_content"] == "draft text"

    def test_refine_phase_adds_content(self):
        result = _build_content_phase_fallback_result("refine", "refined", "s", "r")
        assert result["content"] == "refined"

    def test_assess_phase_adds_assessment_block(self):
        result = _build_content_phase_fallback_result("assess", "assessment", "s", "r")
        assert "assessment" in result
        assert result["assessment"]["quality_score"] == 0.7
        assert result["quality_score"] == 0.7

    def test_image_phase_adds_image_fields(self):
        result = _build_content_phase_fallback_result("image", "img text", "s", "r")
        assert result["image_notes"] == "img text"
        assert result["image_prompt"] == "img text"

    def test_image_selection_phase_adds_image_fields(self):
        result = _build_content_phase_fallback_result("image_selection", "img sel", "s", "r")
        assert "image_notes" in result

    def test_publish_phase_adds_publish_fields(self):
        result = _build_content_phase_fallback_result("publish", "pub content", "s", "r")
        assert result["publish_ready_content"] == "pub content"
        assert result["title"] == "Workflow Generated Draft"
        assert result["summary"] == "pub content"

    def test_finalize_phase_adds_publish_fields(self):
        result = _build_content_phase_fallback_result("finalize", "fin content", "s", "r")
        assert "publish_ready_content" in result

    def test_unknown_phase_only_has_common_fields(self):
        result = _build_content_phase_fallback_result("custom_phase", "text", "s", "r")
        assert "research_findings" not in result
        assert "content" not in result
        assert "assessment" not in result

    def test_summary_truncated_to_240_chars(self):
        long_text = "x" * 500
        result = _build_content_phase_fallback_result("publish", long_text, "s", "r")
        assert len(result["summary"]) == 240
