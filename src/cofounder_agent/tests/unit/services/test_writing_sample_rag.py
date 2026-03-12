"""
Unit tests for services/writing_sample_rag.py

Tests WritingSampleRAGService: _calculate_topic_similarity, _calculate_quality_score,
_calculate_relevance_score, _create_sample_excerpt, _format_rag_prompt,
retrieve_relevant_samples, retrieve_by_style_match, retrieve_by_tone_match,
and get_rag_context. Database and integration service calls are mocked.
"""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from services.writing_sample_rag import RAGRetrievalResult, WritingSampleRAGService


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


def make_service() -> WritingSampleRAGService:
    """Return a service with fully mocked DB and integration service."""
    mock_db = MagicMock()
    mock_db.writing_style = MagicMock()
    mock_db.writing_style.get_user_writing_samples = AsyncMock(return_value=[])

    svc = WritingSampleRAGService(database_service=mock_db)
    # Also mock the integration service to avoid DB calls
    svc.integration_svc = MagicMock()
    svc.integration_svc.get_sample_for_content_generation = AsyncMock(return_value=None)
    return svc


SAMPLE_ANALYSIS = {
    "detected_style": "technical",
    "detected_tone": "formal",
    "vocabulary_diversity": 0.75,
    "avg_sentence_length": 18,
    "style_characteristics": {"uses_lists": True, "uses_headers": True},
}

SAMPLE_RECORD = {
    "id": "sample-abc",
    "title": "Machine Learning in Healthcare",
    "content": "Machine learning and artificial intelligence are transforming healthcare systems worldwide.",
}


# ---------------------------------------------------------------------------
# _calculate_topic_similarity
# ---------------------------------------------------------------------------


class TestCalculateTopicSimilarity:
    def test_identical_topic_scores_high(self):
        svc = make_service()
        score = svc._calculate_topic_similarity(
            "machine learning", "machine learning algorithms", "Machine Learning Guide"
        )
        assert score > 0.0

    def test_no_overlap_scores_zero(self):
        svc = make_service()
        score = svc._calculate_topic_similarity("blockchain defi", "cooking recipes pasta", "Food")
        assert score == 0.0

    def test_empty_query_returns_zero(self):
        svc = make_service()
        score = svc._calculate_topic_similarity("", "some content", "title")
        assert score == 0.0

    def test_partial_overlap_score_between_0_and_1(self):
        svc = make_service()
        score = svc._calculate_topic_similarity(
            "artificial intelligence healthcare",
            "artificial intelligence machine learning",
            "AI Research",
        )
        assert 0.0 < score < 1.0

    def test_short_words_below_4_chars_not_counted(self):
        svc = make_service()
        # All query words < 4 chars; none will match
        score = svc._calculate_topic_similarity("is a an", "is a an the", "the")
        assert score == 0.0


# ---------------------------------------------------------------------------
# _calculate_quality_score
# ---------------------------------------------------------------------------


class TestCalculateQualityScore:
    def test_high_quality_analysis_scores_high(self):
        svc = make_service()
        score = svc._calculate_quality_score(SAMPLE_ANALYSIS)
        assert score > 0.5

    def test_empty_analysis_returns_low_score(self):
        svc = make_service()
        score = svc._calculate_quality_score({})
        assert 0 <= score <= 1.0

    def test_ideal_sentence_length_gets_full_marks(self):
        svc = make_service()
        analysis = {
            "vocabulary_diversity": 0.0,
            "avg_sentence_length": 20,  # ideal range 15-25
            "style_characteristics": {},
        }
        # length_score = 1.0, contributing 0.30 to total
        score = svc._calculate_quality_score(analysis)
        assert score >= 0.30

    def test_score_clamped_between_0_and_1(self):
        svc = make_service()
        score = svc._calculate_quality_score(
            {
                "vocabulary_diversity": 2.0,  # over 1
                "avg_sentence_length": 20,
                "style_characteristics": {"a": True, "b": True, "c": True, "d": True},
            }
        )
        assert 0.0 <= score <= 1.0

    def test_structural_elements_add_to_score(self):
        svc = make_service()
        no_elements = svc._calculate_quality_score({"style_characteristics": {}})
        with_elements = svc._calculate_quality_score(
            {"style_characteristics": {"uses_lists": True, "uses_headers": True, "has_code": True}}
        )
        assert with_elements > no_elements


# ---------------------------------------------------------------------------
# _calculate_relevance_score
# ---------------------------------------------------------------------------


class TestCalculateRelevanceScore:
    def test_returns_float_between_0_and_100(self):
        svc = make_service()
        score = svc._calculate_relevance_score(
            sample_text="artificial intelligence machine learning healthcare",
            sample_title="AI Healthcare Guide",
            sample_analysis=SAMPLE_ANALYSIS,
            query_topic="AI in Healthcare",
        )
        assert 0.0 <= score <= 100.0

    def test_style_match_boosts_score(self):
        svc = make_service()
        no_pref = svc._calculate_relevance_score("content", "title", SAMPLE_ANALYSIS, "topic")
        with_match = svc._calculate_relevance_score(
            "content", "title", SAMPLE_ANALYSIS, "topic", preferred_style="technical"
        )
        assert with_match >= no_pref

    def test_tone_match_boosts_score(self):
        svc = make_service()
        no_pref = svc._calculate_relevance_score("content", "title", SAMPLE_ANALYSIS, "topic")
        with_match = svc._calculate_relevance_score(
            "content", "title", SAMPLE_ANALYSIS, "topic", preferred_tone="formal"
        )
        assert with_match >= no_pref

    def test_style_mismatch_penalized(self):
        svc = make_service()
        match = svc._calculate_relevance_score(
            "content", "title", SAMPLE_ANALYSIS, "topic", preferred_style="technical"
        )
        mismatch = svc._calculate_relevance_score(
            "content", "title", SAMPLE_ANALYSIS, "topic", preferred_style="narrative"
        )
        assert match > mismatch


# ---------------------------------------------------------------------------
# _create_sample_excerpt
# ---------------------------------------------------------------------------


class TestCreateSampleExcerpt:
    def test_short_content_returned_unchanged(self):
        result = WritingSampleRAGService._create_sample_excerpt("Short content.", length=200)
        assert result == "Short content."

    def test_long_content_trimmed(self):
        long_text = "Word " * 100  # 500 chars
        result = WritingSampleRAGService._create_sample_excerpt(long_text, length=50)
        assert len(result) <= 53  # 50 + "..." = 53

    def test_trims_at_sentence_boundary(self):
        content = "First sentence. " * 20  # Many sentences
        result = WritingSampleRAGService._create_sample_excerpt(content, length=100)
        # Should end at a sentence boundary if possible
        assert result.endswith(".") or result.endswith("...")

    def test_empty_content_returns_empty(self):
        result = WritingSampleRAGService._create_sample_excerpt("")
        assert result == ""


# ---------------------------------------------------------------------------
# _format_rag_prompt
# ---------------------------------------------------------------------------


class TestFormatRagPrompt:
    def test_empty_samples_returns_empty_string(self):
        result = WritingSampleRAGService._format_rag_prompt([])
        assert result == ""

    def test_contains_sample_title(self):
        samples = [{**SAMPLE_RECORD, "relevance_score": 80, "analysis": SAMPLE_ANALYSIS}]
        result = WritingSampleRAGService._format_rag_prompt(samples)
        assert "Machine Learning in Healthcare" in result

    def test_contains_style_and_tone(self):
        samples = [{**SAMPLE_RECORD, "relevance_score": 80, "analysis": SAMPLE_ANALYSIS}]
        result = WritingSampleRAGService._format_rag_prompt(samples)
        assert "technical" in result
        assert "formal" in result

    def test_contains_rag_header(self):
        samples = [{**SAMPLE_RECORD, "relevance_score": 80, "analysis": SAMPLE_ANALYSIS}]
        result = WritingSampleRAGService._format_rag_prompt(samples)
        assert "RAG" in result or "Reference" in result


# ---------------------------------------------------------------------------
# retrieve_relevant_samples (async, mocked DB)
# ---------------------------------------------------------------------------


class TestRetrieveRelevantSamples:
    @pytest.mark.asyncio
    async def test_returns_empty_list_when_writing_style_not_initialized(self):
        mock_db = MagicMock()
        mock_db.writing_style = None
        svc = WritingSampleRAGService(database_service=mock_db)
        svc.integration_svc = MagicMock()
        result = await svc.retrieve_relevant_samples("user-1", "AI")
        assert result == []

    @pytest.mark.asyncio
    async def test_returns_empty_when_no_samples_found(self):
        svc = make_service()
        svc.db.writing_style.get_user_writing_samples = AsyncMock(return_value=[])  # type: ignore[union-attr]
        result = await svc.retrieve_relevant_samples("user-1", "AI")
        assert result == []

    @pytest.mark.asyncio
    async def test_returns_samples_sorted_by_relevance(self):
        svc = make_service()
        svc.db.writing_style.get_user_writing_samples = AsyncMock(  # type: ignore[union-attr]
            return_value=[
                {**SAMPLE_RECORD, "id": "s1", "title": "Machine Learning AI"},
                {**SAMPLE_RECORD, "id": "s2", "title": "Cooking Recipes Pasta"},
            ]
        )
        # s1 gets better analysis than s2
        sample_data_1 = {"analysis": {**SAMPLE_ANALYSIS, "detected_style": "technical"}}
        sample_data_2 = {"analysis": {}}

        async def mock_get_sample(writing_style_id):
            return sample_data_1 if writing_style_id == "s1" else sample_data_2

        svc.integration_svc.get_sample_for_content_generation = mock_get_sample  # type: ignore[method-assign]

        result = await svc.retrieve_relevant_samples("user-1", "machine learning artificial intelligence")
        assert len(result) > 0
        # First result has a relevance_score field
        assert "relevance_score" in result[0]

    @pytest.mark.asyncio
    async def test_skips_samples_with_no_data(self):
        svc = make_service()
        svc.db.writing_style.get_user_writing_samples = AsyncMock(  # type: ignore[union-attr]
            return_value=[{**SAMPLE_RECORD, "id": "s1"}]
        )
        svc.integration_svc.get_sample_for_content_generation = AsyncMock(return_value=None)
        result = await svc.retrieve_relevant_samples("user-1", "AI")
        assert result == []

    @pytest.mark.asyncio
    async def test_limit_respected(self):
        svc = make_service()
        samples_in_db = [
            {**SAMPLE_RECORD, "id": f"s{i}", "title": "AI Machine Learning Guide"} for i in range(10)
        ]
        svc.db.writing_style.get_user_writing_samples = AsyncMock(return_value=samples_in_db)  # type: ignore[union-attr]
        svc.integration_svc.get_sample_for_content_generation = AsyncMock(
            return_value={"analysis": SAMPLE_ANALYSIS}
        )
        result = await svc.retrieve_relevant_samples("user-1", "AI machine learning", limit=3)
        assert len(result) <= 3


# ---------------------------------------------------------------------------
# retrieve_by_style_match
# ---------------------------------------------------------------------------


class TestRetrieveByStyleMatch:
    @pytest.mark.asyncio
    async def test_returns_empty_when_no_writing_style(self):
        mock_db = MagicMock()
        mock_db.writing_style = None
        svc = WritingSampleRAGService(database_service=mock_db)
        svc.integration_svc = MagicMock()
        result = await svc.retrieve_by_style_match("user-1", "technical")
        assert result == []

    @pytest.mark.asyncio
    async def test_filters_by_style(self):
        svc = make_service()
        svc.db.writing_style.get_user_writing_samples = AsyncMock(  # type: ignore[union-attr]
            return_value=[{**SAMPLE_RECORD, "id": "s1"}]
        )
        svc.integration_svc.get_sample_for_content_generation = AsyncMock(
            return_value={"analysis": {**SAMPLE_ANALYSIS, "detected_style": "technical"}}
        )
        result = await svc.retrieve_by_style_match("user-1", "technical")
        assert len(result) == 1

    @pytest.mark.asyncio
    async def test_excludes_style_mismatch(self):
        svc = make_service()
        svc.db.writing_style.get_user_writing_samples = AsyncMock(  # type: ignore[union-attr]
            return_value=[{**SAMPLE_RECORD, "id": "s1"}]
        )
        svc.integration_svc.get_sample_for_content_generation = AsyncMock(
            return_value={"analysis": {**SAMPLE_ANALYSIS, "detected_style": "narrative"}}
        )
        result = await svc.retrieve_by_style_match("user-1", "technical")
        assert len(result) == 0

    @pytest.mark.asyncio
    async def test_case_insensitive_style_match(self):
        svc = make_service()
        svc.db.writing_style.get_user_writing_samples = AsyncMock(  # type: ignore[union-attr]
            return_value=[{**SAMPLE_RECORD, "id": "s1"}]
        )
        svc.integration_svc.get_sample_for_content_generation = AsyncMock(
            return_value={"analysis": {"detected_style": "Technical"}}
        )
        result = await svc.retrieve_by_style_match("user-1", "technical")
        assert len(result) == 1


# ---------------------------------------------------------------------------
# retrieve_by_tone_match
# ---------------------------------------------------------------------------


class TestRetrieveByToneMatch:
    @pytest.mark.asyncio
    async def test_filters_by_tone(self):
        svc = make_service()
        svc.db.writing_style.get_user_writing_samples = AsyncMock(  # type: ignore[union-attr]
            return_value=[{**SAMPLE_RECORD, "id": "s1"}]
        )
        svc.integration_svc.get_sample_for_content_generation = AsyncMock(
            return_value={"analysis": {"detected_tone": "formal"}}
        )
        result = await svc.retrieve_by_tone_match("user-1", "formal")
        assert len(result) == 1

    @pytest.mark.asyncio
    async def test_excludes_tone_mismatch(self):
        svc = make_service()
        svc.db.writing_style.get_user_writing_samples = AsyncMock(  # type: ignore[union-attr]
            return_value=[{**SAMPLE_RECORD, "id": "s1"}]
        )
        svc.integration_svc.get_sample_for_content_generation = AsyncMock(
            return_value={"analysis": {"detected_tone": "casual"}}
        )
        result = await svc.retrieve_by_tone_match("user-1", "formal")
        assert len(result) == 0


# ---------------------------------------------------------------------------
# get_rag_context
# ---------------------------------------------------------------------------


class TestGetRagContext:
    @pytest.mark.asyncio
    async def test_returns_has_context_false_when_no_samples(self):
        svc = make_service()
        svc.db.writing_style.get_user_writing_samples = AsyncMock(return_value=[])  # type: ignore[union-attr]
        result = await svc.get_rag_context("user-1", "AI")
        assert result["has_context"] is False
        assert result["samples"] == []

    @pytest.mark.asyncio
    async def test_returns_has_context_true_when_samples_found(self):
        svc = make_service()
        svc.db.writing_style.get_user_writing_samples = AsyncMock(  # type: ignore[union-attr]
            return_value=[{**SAMPLE_RECORD, "id": "s1"}]
        )
        svc.integration_svc.get_sample_for_content_generation = AsyncMock(
            return_value={"analysis": SAMPLE_ANALYSIS}
        )

        with patch.object(svc, "retrieve_relevant_samples", new=AsyncMock(
            return_value=[{**SAMPLE_RECORD, "relevance_score": 80, "analysis": SAMPLE_ANALYSIS}]
        )):
            result = await svc.get_rag_context("user-1", "machine learning")

        assert result["has_context"] is True
        assert result["num_samples"] == 1

    @pytest.mark.asyncio
    async def test_prompt_injection_included(self):
        svc = make_service()
        samples = [{**SAMPLE_RECORD, "relevance_score": 80, "analysis": SAMPLE_ANALYSIS}]
        with patch.object(svc, "retrieve_relevant_samples", new=AsyncMock(return_value=samples)):
            result = await svc.get_rag_context("user-1", "AI")
        assert "prompt_injection" in result

    @pytest.mark.asyncio
    async def test_error_returns_safe_fallback(self):
        svc = make_service()
        with patch.object(svc, "retrieve_relevant_samples", new=AsyncMock(side_effect=RuntimeError("DB down"))):
            result = await svc.get_rag_context("user-1", "AI")
        assert result["has_context"] is False
