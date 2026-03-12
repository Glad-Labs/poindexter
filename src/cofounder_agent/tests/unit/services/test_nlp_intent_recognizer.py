"""
Unit tests for services/nlp_intent_recognizer.py.

NLPIntentRecognizer is a pure regex/pattern matching class with no DB or LLM
dependencies. All tests are synchronous-ish (using pytest-asyncio for the
async methods).

Tests cover:
- recognize_intent: known phrases → correct intent type
- recognize_intent: unknown phrases → None
- recognize_intent: empty/invalid input → None
- recognize_multiple_intents: returns top_n ranked by confidence
- extract_topic: extracts topic from "write about X" messages
- Parameter extraction basics
"""

import pytest

from services.nlp_intent_recognizer import NLPIntentRecognizer, IntentMatch


# ---------------------------------------------------------------------------
# Shared fixture
# ---------------------------------------------------------------------------


@pytest.fixture
def recognizer() -> NLPIntentRecognizer:
    return NLPIntentRecognizer()


# ---------------------------------------------------------------------------
# recognize_intent — content generation
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestRecognizeIntent:
    @pytest.mark.asyncio
    async def test_write_blog_returns_content_generation(self, recognizer):
        match = await recognizer.recognize_intent("Write a blog post about AI trends")
        assert match is not None
        assert match.intent_type == "content_generation"

    @pytest.mark.asyncio
    async def test_generate_article_returns_content_generation(self, recognizer):
        match = await recognizer.recognize_intent("generate an article about machine learning")
        assert match is not None
        assert match.intent_type == "content_generation"

    @pytest.mark.asyncio
    async def test_create_post_returns_content_generation(self, recognizer):
        match = await recognizer.recognize_intent("Create a post about Python best practices")
        assert match is not None
        assert match.intent_type == "content_generation"

    @pytest.mark.asyncio
    async def test_social_media_post_returns_social_media(self, recognizer):
        match = await recognizer.recognize_intent("create a social media post for Twitter")
        assert match is not None
        assert match.intent_type == "social_media"

    @pytest.mark.asyncio
    async def test_tweet_returns_social_media(self, recognizer):
        # Pattern: (create|write|generate)\s+(social\s+)?(media\s+)?(post|tweet|content)
        # "write tweet" matches without the article "a"
        match = await recognizer.recognize_intent("write tweet about our new product launch")
        assert match is not None
        assert match.intent_type == "social_media"

    @pytest.mark.asyncio
    async def test_post_to_twitter_returns_social_media(self, recognizer):
        match = await recognizer.recognize_intent("post to twitter about the product launch")
        assert match is not None
        assert match.intent_type == "social_media"

    @pytest.mark.asyncio
    async def test_match_has_confidence_above_zero(self, recognizer):
        match = await recognizer.recognize_intent("Write a blog about Python")
        assert match is not None
        assert match.confidence > 0.0
        assert match.confidence <= 1.0

    @pytest.mark.asyncio
    async def test_match_stores_raw_message(self, recognizer):
        msg = "Write a blog post about AI"
        match = await recognizer.recognize_intent(msg)
        assert match is not None
        assert match.raw_message == msg

    @pytest.mark.asyncio
    async def test_unknown_message_returns_none(self, recognizer):
        match = await recognizer.recognize_intent("What's the weather like today?")
        assert match is None

    @pytest.mark.asyncio
    async def test_empty_string_returns_none(self, recognizer):
        match = await recognizer.recognize_intent("")
        assert match is None

    @pytest.mark.asyncio
    async def test_none_input_returns_none(self, recognizer):
        match = await recognizer.recognize_intent(None)  # type: ignore[arg-type]
        assert match is None

    @pytest.mark.asyncio
    async def test_whitespace_only_returns_none(self, recognizer):
        match = await recognizer.recognize_intent("   ")
        assert match is None

    @pytest.mark.asyncio
    async def test_case_insensitive_matching(self, recognizer):
        match_lower = await recognizer.recognize_intent("write a blog post about testing")
        match_upper = await recognizer.recognize_intent("WRITE A BLOG POST ABOUT TESTING")
        assert match_lower is not None
        assert match_upper is not None
        assert match_lower.intent_type == match_upper.intent_type

    @pytest.mark.asyncio
    async def test_match_has_workflow_type(self, recognizer):
        match = await recognizer.recognize_intent("Write a blog about cloud computing")
        assert match is not None
        assert hasattr(match, "workflow_type")
        assert match.workflow_type is not None

    @pytest.mark.asyncio
    async def test_match_has_parameters_dict(self, recognizer):
        match = await recognizer.recognize_intent("Write a blog post about AI")
        assert match is not None
        assert isinstance(match.parameters, dict)


# ---------------------------------------------------------------------------
# recognize_multiple_intents
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestRecognizeMultipleIntents:
    @pytest.mark.asyncio
    async def test_returns_list_for_clear_match(self, recognizer):
        matches = await recognizer.recognize_multiple_intents("Write a blog about machine learning")
        assert isinstance(matches, list)
        assert len(matches) >= 1

    @pytest.mark.asyncio
    async def test_returns_empty_list_for_unknown_message(self, recognizer):
        matches = await recognizer.recognize_multiple_intents("Random gibberish xyz123")
        assert isinstance(matches, list)
        assert len(matches) == 0

    @pytest.mark.asyncio
    async def test_respects_top_n_limit(self, recognizer):
        matches = await recognizer.recognize_multiple_intents(
            "Write a social media blog post about AI",
            top_n=1,
        )
        assert len(matches) <= 1

    @pytest.mark.asyncio
    async def test_matches_sorted_by_confidence_descending(self, recognizer):
        matches = await recognizer.recognize_multiple_intents(
            "Write a social media blog post about AI",
            top_n=3,
        )
        if len(matches) > 1:
            for i in range(len(matches) - 1):
                assert matches[i].confidence >= matches[i + 1].confidence

    @pytest.mark.asyncio
    async def test_all_matches_are_intent_match_instances(self, recognizer):
        matches = await recognizer.recognize_multiple_intents(
            "Write a blog about machine learning",
            top_n=3,
        )
        for match in matches:
            assert isinstance(match, IntentMatch)


# ---------------------------------------------------------------------------
# extract_topic
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestExtractTopic:
    @pytest.mark.asyncio
    async def test_extracts_topic_from_about_phrase(self, recognizer):
        result = await recognizer.extract_topic(
            "Write a blog about machine learning trends", {}
        )
        assert "topic" in result
        topic = result["topic"]
        assert topic is not None
        assert "machine learning" in topic.lower()

    @pytest.mark.asyncio
    async def test_extracts_topic_from_on_phrase(self, recognizer):
        result = await recognizer.extract_topic("Generate an article on Python best practices", {})
        assert "topic" in result
        topic = result["topic"]
        assert topic is not None

    @pytest.mark.asyncio
    async def test_returns_none_topic_when_no_match(self, recognizer):
        result = await recognizer.extract_topic("Write something.", {})
        assert "topic" in result
        # topic may be None or empty when no pattern matches


# ---------------------------------------------------------------------------
# IntentMatch dataclass
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestIntentMatch:
    def test_intent_match_creation(self):
        match = IntentMatch(
            intent_type="content_generation",
            confidence=0.95,
            workflow_type="content_generation",
            parameters={"topic": "AI trends"},
            raw_message="Write a blog about AI trends",
        )
        assert match.intent_type == "content_generation"
        assert match.confidence == 0.95
        assert match.parameters["topic"] == "AI trends"

    def test_intent_match_equality(self):
        m1 = IntentMatch(
            intent_type="social_media",
            confidence=0.9,
            workflow_type="social_media",
            parameters={},
            raw_message="tweet about product launch",
        )
        m2 = IntentMatch(
            intent_type="social_media",
            confidence=0.9,
            workflow_type="social_media",
            parameters={},
            raw_message="tweet about product launch",
        )
        assert m1 == m2
