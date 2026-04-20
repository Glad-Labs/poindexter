"""
Unit tests for WritingStyleIntegrationService.

Tests focus on the pure-logic, synchronous methods:
  - _analyze_sample (text metrics, tone detection, style detection)
  - _build_analysis_guidance (prompt text assembly)
  - _compare_analyses (two-dict comparison)

Async methods that call the DB (get_sample_for_content_generation,
generate_creative_agent_prompt_injection, verify_style_match) are tested
with a mocked WritingStyleService so no real I/O occurs.
"""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from services.writing_style_integration import WritingStyleIntegrationService

# ---------------------------------------------------------------------------
# Fixtures / Helpers
# ---------------------------------------------------------------------------


def make_service() -> WritingStyleIntegrationService:
    """Return a WritingStyleIntegrationService with a mock DatabaseService."""
    mock_db = MagicMock()
    # Patch WritingStyleService so __init__ doesn't need a real DB
    with patch("services.writing_style_integration.WritingStyleService"):
        svc = WritingStyleIntegrationService(mock_db)
    return svc


@pytest.fixture
def service() -> WritingStyleIntegrationService:
    return make_service()


FORMAL_SAMPLE = (
    "Therefore, it is noteworthy that this comprehensive analysis demonstrates "
    "significant implications. Furthermore, the findings facilitate a deeper "
    "understanding. Consequently, utilization of these insights will prove beneficial. "
    "The organization utilizes advanced methodologies to achieve objectives."
)

CASUAL_SAMPLE = (
    "Like, this is really awesome and super cool! "
    "I actually think it's basically the best. "
    "Literally everyone loves it, it's kinda great. "
    "I really wanna try it again."
)

TECHNICAL_SAMPLE = (
    "```python\nfor i in range(10):\n    print(i)\n```\n"
    "# Overview\nThe algorithm implements a recursive function architecture.\n"
    "# Details\nSee the code block above for the implementation.\n"
)

LISTICLE_SAMPLE = (
    "- First benefit of the approach\n"
    "- Second reason to adopt it\n"
    "- Third measurable outcome\n"
    "- Fourth key advantage\n"
    "- Fifth step to implementation\n"
)

NARRATIVE_SAMPLE = (
    "For instance, consider the experience of a typical startup founder. "
    "Such as the challenges they face each day. "
    "The story reveals their perseverance and insight. "
    "They explored many paths before finding the right one."
)


# ---------------------------------------------------------------------------
# _analyze_sample — basic metrics
# ---------------------------------------------------------------------------


class TestAnalyzeSampleMetrics:
    def test_empty_string_returns_empty_dict(self, service):
        result = service._analyze_sample("")
        assert result == {}

    def test_word_count_correct(self, service):
        result = service._analyze_sample("one two three four five")
        assert result["word_count"] == 5

    def test_sentence_count_correct(self, service):
        result = service._analyze_sample("Hello world. How are you? Fine.")
        assert result["sentence_count"] == 3

    def test_paragraph_count_correct(self, service):
        text = "First paragraph.\n\nSecond paragraph.\n\nThird paragraph."
        result = service._analyze_sample(text)
        assert result["paragraph_count"] == 3

    def test_avg_word_length_positive(self, service):
        result = service._analyze_sample("short long extraordinary")
        assert result["avg_word_length"] > 0

    def test_avg_sentence_length_positive(self, service):
        result = service._analyze_sample("Hello world. How are you? Fine thanks.")
        assert result["avg_sentence_length"] > 0

    def test_vocabulary_diversity_0_to_1(self, service):
        result = service._analyze_sample("the the the cat sat on the mat")
        assert 0.0 <= result["vocabulary_diversity"] <= 1.0

    def test_all_required_keys_present(self, service):
        result = service._analyze_sample("A simple sentence.")
        expected_keys = {
            "detected_tone",
            "detected_style",
            "tone_scores",
            "word_count",
            "sentence_count",
            "paragraph_count",
            "avg_word_length",
            "avg_sentence_length",
            "avg_paragraph_length",
            "vocabulary_diversity",
            "style_characteristics",
        }
        assert expected_keys.issubset(result.keys())


# ---------------------------------------------------------------------------
# _analyze_sample — tone detection
# ---------------------------------------------------------------------------


class TestAnalyzeSampleTone:
    def test_formal_tone_detected(self, service):
        result = service._analyze_sample(FORMAL_SAMPLE)
        assert result["detected_tone"] == "formal"

    def test_casual_tone_detected(self, service):
        result = service._analyze_sample(CASUAL_SAMPLE)
        assert result["detected_tone"] == "casual"

    def test_authoritative_tone_detected(self, service):
        authoritative = (
            "Research shows that studies demonstrate clear results. "
            "Based on evidence, it has been proven and documented. "
            "According to established literature, confirmed data validates this. "
            "Analysis indicates a consistent pattern."
        )
        result = service._analyze_sample(authoritative)
        assert result["detected_tone"] == "authoritative"

    def test_neutral_when_no_markers(self, service):
        plain = "The cat sat on the mat. It was a warm day outside."
        result = service._analyze_sample(plain)
        assert result["detected_tone"] == "neutral"

    def test_tone_scores_dict_has_four_entries(self, service):
        result = service._analyze_sample("Any text here.")
        assert len(result["tone_scores"]) == 4


# ---------------------------------------------------------------------------
# _analyze_sample — style detection
# ---------------------------------------------------------------------------


class TestAnalyzeSampleStyle:
    def test_technical_style_detected(self, service):
        result = service._analyze_sample(TECHNICAL_SAMPLE)
        assert result["detected_style"] == "technical"

    def test_listicle_style_detected(self, service):
        result = service._analyze_sample(LISTICLE_SAMPLE)
        assert result["detected_style"] == "listicle"

    def test_narrative_style_detected(self, service):
        result = service._analyze_sample(NARRATIVE_SAMPLE)
        assert result["detected_style"] == "narrative"


# ---------------------------------------------------------------------------
# _analyze_sample — style_characteristics flags
# ---------------------------------------------------------------------------


class TestAnalyzeSampleStyleCharacteristics:
    def test_has_lists_true(self, service):
        result = service._analyze_sample("- Item one\n- Item two")
        assert result["style_characteristics"]["has_lists"] is True

    def test_has_lists_false(self, service):
        result = service._analyze_sample("No lists here at all.")
        assert result["style_characteristics"]["has_lists"] is False

    def test_has_code_blocks_true(self, service):
        result = service._analyze_sample("```python\nprint('hi')\n```")
        assert result["style_characteristics"]["has_code_blocks"] is True

    def test_has_code_blocks_false(self, service):
        result = service._analyze_sample("Plain text without code.")
        assert result["style_characteristics"]["has_code_blocks"] is False

    def test_has_headings_true(self, service):
        result = service._analyze_sample("# Title\nSome content.")
        assert result["style_characteristics"]["has_headings"] is True

    def test_has_headings_false(self, service):
        result = service._analyze_sample("No headings here at all.")
        assert result["style_characteristics"]["has_headings"] is False

    def test_has_examples_true(self, service):
        result = service._analyze_sample("For example, consider this case.")
        assert result["style_characteristics"]["has_examples"] is True

    def test_has_examples_false(self, service):
        result = service._analyze_sample("The sky is blue today.")
        assert result["style_characteristics"]["has_examples"] is False

    def test_has_quotes_true(self, service):
        result = service._analyze_sample('He said "hello world" to everyone.')
        assert result["style_characteristics"]["has_quotes"] is True


# ---------------------------------------------------------------------------
# _build_analysis_guidance
# ---------------------------------------------------------------------------


class TestBuildAnalysisGuidance:
    def test_empty_analysis_returns_empty(self, service):
        result = WritingStyleIntegrationService._build_analysis_guidance({})
        assert result == ""

    def test_detected_tone_in_output(self, service):
        analysis = {
            "detected_tone": "formal",
            "detected_style": "technical",
            "avg_sentence_length": 15.0,
            "vocabulary_diversity": 0.5,
        }
        result = WritingStyleIntegrationService._build_analysis_guidance(analysis)
        assert "formal" in result

    def test_detected_style_in_output(self, service):
        analysis = {
            "detected_tone": "casual",
            "detected_style": "narrative",
            "avg_sentence_length": 12.0,
            "vocabulary_diversity": 0.4,
        }
        result = WritingStyleIntegrationService._build_analysis_guidance(analysis)
        assert "narrative" in result

    def test_sentence_length_in_output(self, service):
        analysis = {
            "detected_tone": "formal",
            "detected_style": "technical",
            "avg_sentence_length": 18.5,
            "vocabulary_diversity": 0.6,
        }
        result = WritingStyleIntegrationService._build_analysis_guidance(analysis)
        assert "18.5" in result

    def test_vocabulary_diversity_formatted_as_percent(self, service):
        analysis = {
            "detected_tone": "formal",
            "detected_style": "technical",
            "avg_sentence_length": 15.0,
            "vocabulary_diversity": 0.75,
        }
        result = WritingStyleIntegrationService._build_analysis_guidance(analysis)
        assert "75.0%" in result or "75%" in result

    def test_headings_flag_in_output_when_true(self, service):
        analysis = {
            "detected_tone": "formal",
            "detected_style": "technical",
            "avg_sentence_length": 15.0,
            "vocabulary_diversity": 0.5,
            "style_characteristics": {
                "has_headings": True,
                "has_lists": False,
                "has_examples": False,
                "has_quotes": False,
                "has_code_blocks": False,
            },
        }
        result = WritingStyleIntegrationService._build_analysis_guidance(analysis)
        assert "heading" in result.lower()

    def test_lists_flag_in_output_when_true(self, service):
        analysis = {
            "detected_tone": "casual",
            "detected_style": "listicle",
            "avg_sentence_length": 10.0,
            "vocabulary_diversity": 0.4,
            "style_characteristics": {
                "has_headings": False,
                "has_lists": True,
                "has_examples": False,
                "has_quotes": False,
                "has_code_blocks": False,
            },
        }
        result = WritingStyleIntegrationService._build_analysis_guidance(analysis)
        assert "list" in result.lower()

    def test_false_flags_not_in_output(self, service):
        analysis = {
            "detected_tone": "formal",
            "detected_style": "technical",
            "avg_sentence_length": 15.0,
            "vocabulary_diversity": 0.5,
            "style_characteristics": {
                "has_headings": False,
                "has_lists": False,
                "has_examples": False,
                "has_quotes": False,
                "has_code_blocks": False,
            },
        }
        result = WritingStyleIntegrationService._build_analysis_guidance(analysis)
        assert "heading" not in result.lower()
        assert "list" not in result.lower()


# ---------------------------------------------------------------------------
# _compare_analyses
# ---------------------------------------------------------------------------


class TestCompareAnalyses:
    def _matching_analysis(self) -> dict:
        return {
            "detected_tone": "formal",
            "detected_style": "technical",
            "avg_sentence_length": 15.0,
        }

    def test_tone_match_true_when_equal(self, service):
        a = self._matching_analysis()
        b = self._matching_analysis()
        result = WritingStyleIntegrationService._compare_analyses(a, b)
        assert result["tone_match"] is True

    def test_tone_match_false_when_different(self, service):
        a = self._matching_analysis()
        b = {**self._matching_analysis(), "detected_tone": "casual"}
        result = WritingStyleIntegrationService._compare_analyses(a, b)
        assert result["tone_match"] is False

    def test_style_match_true_when_equal(self, service):
        a = self._matching_analysis()
        b = self._matching_analysis()
        result = WritingStyleIntegrationService._compare_analyses(a, b)
        assert result["style_match"] is True

    def test_style_match_false_when_different(self, service):
        a = self._matching_analysis()
        b = {**self._matching_analysis(), "detected_style": "narrative"}
        result = WritingStyleIntegrationService._compare_analyses(a, b)
        assert result["style_match"] is False

    def test_sentence_length_similarity_true_when_close(self, service):
        a = {"detected_tone": "formal", "detected_style": "technical", "avg_sentence_length": 15.0}
        b = {"detected_tone": "formal", "detected_style": "technical", "avg_sentence_length": 18.0}
        result = WritingStyleIntegrationService._compare_analyses(a, b)
        assert result["sentence_length_similarity"] is True

    def test_sentence_length_similarity_false_when_far(self, service):
        a = {"detected_tone": "formal", "detected_style": "technical", "avg_sentence_length": 5.0}
        b = {"detected_tone": "formal", "detected_style": "technical", "avg_sentence_length": 30.0}
        result = WritingStyleIntegrationService._compare_analyses(a, b)
        assert result["sentence_length_similarity"] is False

    def test_result_contains_all_expected_keys(self, service):
        a = self._matching_analysis()
        b = self._matching_analysis()
        result = WritingStyleIntegrationService._compare_analyses(a, b)
        expected_keys = {
            "tone_match",
            "tone_sample",
            "tone_generated",
            "style_match",
            "style_sample",
            "style_generated",
            "sentence_length_similarity",
            "sample_sentence_length",
            "generated_sentence_length",
        }
        assert expected_keys.issubset(result.keys())


# ---------------------------------------------------------------------------
# get_sample_for_content_generation (async, mocked DB)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
class TestGetSampleForContentGeneration:
    async def test_returns_none_when_no_ids(self):
        svc = make_service()
        result = await svc.get_sample_for_content_generation(None, None)
        assert result is None

    async def test_calls_writing_style_service_with_style_id(self):
        svc = make_service()
        mock_sample = {
            "sample_text": FORMAL_SAMPLE,
            "writing_style_guidance": "Be formal.",
        }
        svc.writing_style_service.get_style_prompt_for_specific_sample = AsyncMock(
            return_value=mock_sample
        )
        result = await svc.get_sample_for_content_generation("style-uuid-123")
        assert result is not None
        assert "analysis" in result
        svc.writing_style_service.get_style_prompt_for_specific_sample.assert_called_once_with(
            "style-uuid-123"
        )

    async def test_falls_back_to_user_id_when_no_style_id(self):
        svc = make_service()
        mock_sample = {"sample_text": CASUAL_SAMPLE}
        svc.writing_style_service.get_style_prompt_for_generation = AsyncMock(
            return_value=mock_sample
        )
        result = await svc.get_sample_for_content_generation(None, "user-123")
        assert result is not None
        svc.writing_style_service.get_style_prompt_for_generation.assert_called_once_with(
            "user-123"
        )

    async def test_returns_none_when_service_returns_none(self):
        svc = make_service()
        svc.writing_style_service.get_style_prompt_for_specific_sample = AsyncMock(
            return_value=None
        )
        result = await svc.get_sample_for_content_generation("style-uuid-999")
        assert result is None

    async def test_analysis_appended_to_sample_data(self):
        svc = make_service()
        svc.writing_style_service.get_style_prompt_for_specific_sample = AsyncMock(
            return_value={"sample_text": FORMAL_SAMPLE}
        )
        result = await svc.get_sample_for_content_generation("style-uuid-abc")
        assert result is not None
        assert "analysis" in result
        assert "detected_tone" in result["analysis"]

    async def test_returns_none_on_exception(self):
        svc = make_service()
        svc.writing_style_service.get_style_prompt_for_specific_sample = AsyncMock(
            side_effect=RuntimeError("db down")
        )
        result = await svc.get_sample_for_content_generation("style-uuid-err")
        assert result is None


# ---------------------------------------------------------------------------
# generate_creative_agent_prompt_injection (async, mocked)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
class TestGenerateCreativeAgentPromptInjection:
    async def test_returns_base_prompt_when_no_sample(self):
        svc = make_service()
        svc.writing_style_service.get_style_prompt_for_specific_sample = AsyncMock(
            return_value=None
        )
        result = await svc.generate_creative_agent_prompt_injection(None, None, "Base prompt here.")
        assert result == "Base prompt here."

    async def test_enhances_prompt_with_guidance(self):
        svc = make_service()
        svc.writing_style_service.get_style_prompt_for_specific_sample = AsyncMock(
            return_value={
                "sample_text": FORMAL_SAMPLE,
                "writing_style_guidance": "STYLE GUIDANCE: Be formal.",
            }
        )
        result = await svc.generate_creative_agent_prompt_injection(
            "style-id", None, "Base prompt."
        )
        assert "Base prompt" in result
        assert "STYLE GUIDANCE" in result

    async def test_returns_base_prompt_on_exception(self):
        svc = make_service()
        svc.writing_style_service.get_style_prompt_for_specific_sample = AsyncMock(
            side_effect=Exception("unexpected error")
        )
        result = await svc.generate_creative_agent_prompt_injection(
            "style-id", None, "Safe base prompt."
        )
        assert result == "Safe base prompt."


# ---------------------------------------------------------------------------
# verify_style_match (async, mocked)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
class TestVerifyStyleMatch:
    async def test_returns_not_matched_when_sample_not_found(self):
        svc = make_service()
        svc.writing_style_service.get_style_prompt_for_specific_sample = AsyncMock(
            return_value=None
        )
        result = await svc.verify_style_match("generated content", "style-id")
        assert result["matched"] is False

    async def test_returns_comparison_when_sample_found(self):
        svc = make_service()
        svc.writing_style_service.get_style_prompt_for_specific_sample = AsyncMock(
            return_value={"sample_text": FORMAL_SAMPLE}
        )
        result = await svc.verify_style_match(FORMAL_SAMPLE, "style-id")
        assert "comparison" in result
        assert "sample_analysis" in result
        assert "generated_analysis" in result

    async def test_returns_not_matched_on_exception(self):
        svc = make_service()
        svc.writing_style_service.get_style_prompt_for_specific_sample = AsyncMock(
            side_effect=RuntimeError("crash")
        )
        result = await svc.verify_style_match("content", "style-id")
        assert result["matched"] is False
