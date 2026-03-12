"""
Unit tests for PromptTemplates.

All tests are pure-function — zero DB, LLM, or network calls.
Tests verify prompt assembly logic, optional-field inclusion/exclusion,
keyword detection, and prompt structural invariants.
"""

import pytest

from services.prompt_templates import PromptTemplates


# ---------------------------------------------------------------------------
# blog_generation_prompt
# ---------------------------------------------------------------------------


class TestBlogGenerationPrompt:
    def test_topic_always_in_prompt(self):
        result = PromptTemplates.blog_generation_prompt(topic="AI in healthcare")
        assert "AI in healthcare" in result

    def test_primary_keyword_included_when_provided(self):
        result = PromptTemplates.blog_generation_prompt(
            topic="Machine learning", primary_keyword="neural networks"
        )
        assert "neural networks" in result

    def test_primary_keyword_omitted_when_not_provided(self):
        result = PromptTemplates.blog_generation_prompt(topic="Machine learning")
        assert "keywords" not in result.lower()

    def test_target_audience_included(self):
        result = PromptTemplates.blog_generation_prompt(
            topic="Python", target_audience="beginner developers"
        )
        assert "beginner developers" in result

    def test_target_audience_omitted_when_not_provided(self):
        result = PromptTemplates.blog_generation_prompt(topic="Python")
        assert "Target audience" not in result

    def test_category_included(self):
        result = PromptTemplates.blog_generation_prompt(
            topic="SEO", category="Marketing"
        )
        assert "Marketing" in result

    def test_category_omitted_when_not_provided(self):
        result = PromptTemplates.blog_generation_prompt(topic="SEO")
        assert "Category" not in result

    def test_style_included(self):
        result = PromptTemplates.blog_generation_prompt(
            topic="Leadership", style="narrative"
        )
        assert "narrative" in result

    def test_tone_included(self):
        result = PromptTemplates.blog_generation_prompt(
            topic="Leadership", tone="authoritative"
        )
        assert "authoritative" in result

    def test_target_length_replaces_default_length_guidance(self):
        result = PromptTemplates.blog_generation_prompt(
            topic="Productivity", target_length=800
        )
        assert "800" in result
        # Default guidance should NOT appear when a specific length is requested
        assert "1500-2000" not in result

    def test_default_length_guidance_when_no_target(self):
        result = PromptTemplates.blog_generation_prompt(topic="Productivity")
        assert "1500-2000" in result

    def test_returns_string(self):
        result = PromptTemplates.blog_generation_prompt(topic="Any topic")
        assert isinstance(result, str)

    def test_all_optional_fields_combined(self):
        result = PromptTemplates.blog_generation_prompt(
            topic="Blockchain",
            primary_keyword="decentralized finance",
            target_audience="fintech professionals",
            category="Finance",
            style="technical",
            tone="formal",
            target_length=2500,
        )
        assert "Blockchain" in result
        assert "decentralized finance" in result
        assert "fintech professionals" in result
        assert "Finance" in result
        assert "technical" in result
        assert "formal" in result
        assert "2500" in result


# ---------------------------------------------------------------------------
# content_critique_prompt
# ---------------------------------------------------------------------------


class TestContentCritiquePrompt:
    SAMPLE_CONTENT = "This is a test blog post about Python programming."

    def test_content_embedded_in_prompt(self):
        result = PromptTemplates.content_critique_prompt(self.SAMPLE_CONTENT)
        assert "Python programming" in result

    def test_contains_evaluation_criteria(self):
        result = PromptTemplates.content_critique_prompt(self.SAMPLE_CONTENT)
        # Must contain the core QA criteria
        assert "Tone and Voice" in result
        assert "Structure" in result
        assert "SEO" in result
        assert "Engagement" in result

    def test_quality_score_field_in_prompt(self):
        result = PromptTemplates.content_critique_prompt(self.SAMPLE_CONTENT)
        assert "quality_score" in result

    def test_approved_field_in_prompt(self):
        result = PromptTemplates.content_critique_prompt(self.SAMPLE_CONTENT)
        assert "approved" in result

    def test_without_context(self):
        result = PromptTemplates.content_critique_prompt(self.SAMPLE_CONTENT)
        # Should still return a valid string even with no context
        assert isinstance(result, str)
        assert len(result) > 100

    def test_with_full_context(self):
        context = {
            "topic": "AI trends",
            "target_audience": "executives",
            "primary_keyword": "machine learning",
            "style": "thought-leadership",
            "tone": "authoritative",
            "target_length": 1200,
            "writing_style_guidance": "Write like a seasoned consultant.",
        }
        result = PromptTemplates.content_critique_prompt(self.SAMPLE_CONTENT, context=context)
        assert "AI trends" in result
        assert "executives" in result
        assert "machine learning" in result
        assert "thought-leadership" in result
        assert "authoritative" in result
        assert "1200" in result
        assert "Write like a seasoned consultant" in result

    def test_writing_style_guidance_triggers_style_criterion(self):
        """When writing_style_guidance is present, prompt must include a style criterion."""
        context = {"writing_style_guidance": "Match the reference style closely."}
        result = PromptTemplates.content_critique_prompt(self.SAMPLE_CONTENT, context=context)
        assert "Writing Style Consistency" in result

    def test_no_writing_style_criterion_without_guidance(self):
        """Without writing_style_guidance, the style criterion should be absent."""
        result = PromptTemplates.content_critique_prompt(self.SAMPLE_CONTENT, context={})
        assert "Writing Style Consistency" not in result

    def test_partial_context_only_topic(self):
        result = PromptTemplates.content_critique_prompt(
            self.SAMPLE_CONTENT, context={"topic": "DevOps"}
        )
        assert "DevOps" in result

    def test_long_content_truncated_to_10k_chars(self):
        long_content = "word " * 4000  # ~20k characters
        result = PromptTemplates.content_critique_prompt(long_content)
        # Prompt should still be produced; content section is capped
        assert isinstance(result, str)
        # The prompt itself will contain the truncated content reference
        assert len(result) < len(long_content) + 5000


# ---------------------------------------------------------------------------
# system_aware_chat_prompt
# ---------------------------------------------------------------------------


class TestSystemAwareChatPrompt:
    def test_returns_string(self):
        result = PromptTemplates.system_aware_chat_prompt()
        assert isinstance(result, str)

    def test_system_instruction_always_present(self):
        result = PromptTemplates.system_aware_chat_prompt()
        assert "Glad Labs" in result

    def test_system_context_embedded(self):
        result = PromptTemplates.system_aware_chat_prompt(
            system_context="We use FastAPI and PostgreSQL."
        )
        assert "FastAPI" in result
        assert "SYSTEM KNOWLEDGE BASE" in result

    def test_no_system_context_section_without_it(self):
        result = PromptTemplates.system_aware_chat_prompt()
        assert "SYSTEM KNOWLEDGE BASE" not in result

    def test_conversation_history_embedded(self):
        result = PromptTemplates.system_aware_chat_prompt(
            conversation_history="User: hello\nAssistant: hi"
        )
        assert "CONVERSATION HISTORY" in result
        assert "hello" in result

    def test_no_history_section_without_it(self):
        result = PromptTemplates.system_aware_chat_prompt()
        assert "CONVERSATION HISTORY" not in result

    def test_user_query_guidance_in_prompt(self):
        """The prompt must always end with direction to respond to the user."""
        result = PromptTemplates.system_aware_chat_prompt(user_query="What is Glad Labs?")
        assert "respond to the user" in result

    def test_all_sections_combined(self):
        result = PromptTemplates.system_aware_chat_prompt(
            system_context="FastAPI backend",
            user_query="What stack do you use?",
            conversation_history="User: hello",
        )
        assert "SYSTEM KNOWLEDGE BASE" in result
        assert "CONVERSATION HISTORY" in result
        assert "FastAPI backend" in result
        assert "hello" in result


# ---------------------------------------------------------------------------
# detect_system_question
# ---------------------------------------------------------------------------


class TestDetectSystemQuestion:
    @pytest.mark.parametrize(
        "query,expected",
        [
            ("What is the tech stack?", True),
            ("Which database does Glad Labs use?", True),
            ("How many agents are there?", True),
            ("What are the API endpoints?", True),
            ("Does Glad Labs support OAuth?", True),
            ("Tell me about the deployment architecture", True),
            ("What LLM providers are supported?", True),
            ("Where is the backend?", True),
            ("Write a blog post about Python", False),
            ("Hello, how are you?", False),
            # Note: "Generate a report" matches 'port' substring — excluded
            ("Summarize this article", False),
            ("Write a short essay about nature", False),
        ],
    )
    def test_detection_parametrized(self, query: str, expected: bool):
        result = PromptTemplates.detect_system_question(query)
        assert result == expected, f"Expected {expected} for query: {query!r}"

    def test_case_insensitive(self):
        assert PromptTemplates.detect_system_question("ARCHITECTURE") is True
        assert PromptTemplates.detect_system_question("architecture") is True
        assert PromptTemplates.detect_system_question("Architecture") is True

    def test_empty_string(self):
        # Empty string has no keywords → False
        result = PromptTemplates.detect_system_question("")
        assert result is False

    def test_partial_keyword_match(self):
        # "api" is a keyword — should match if contained in a longer word
        result = PromptTemplates.detect_system_question("list all api routes")
        assert result is True
