"""
Unit tests for agents/content_agent/agents/creative_agent.py — CreativeAgent

Tests focus on:
- _extract_asset(): regex extraction from text
- _clean_llm_output(): markdown heading normalization
- CreativeAgent.run(): refinement guard logic (no LLM calls)
- _generate_seo_assets(): JSON parsing / fallback logic
"""

import json
import os
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

os.environ.setdefault("DATABASE_URL", "postgresql://test:test@localhost:5432/test")
os.environ.setdefault("ANTHROPIC_API_KEY", "test-placeholder")


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def make_blog_post(**kwargs):
    """Build a BlogPost Pydantic model with sensible defaults."""
    from agents.content_agent.utils.data_models import BlogPost

    defaults = {
        "topic": "AI Trends",
        "primary_keyword": "artificial intelligence",
        "target_audience": "Tech professionals",
        "category": "Technology",
    }
    defaults.update(kwargs)
    return BlogPost(**defaults)  # type: ignore[arg-type]


def make_agent():
    """Construct a CreativeAgent bypassing LLM and tool initialization."""
    with (
        patch("agents.content_agent.agents.creative_agent.CrewAIToolsFactory") as mock_tools,
        patch("agents.content_agent.agents.creative_agent.get_prompt_manager") as mock_pm,
    ):
        mock_tools.get_content_agent_tools.return_value = []
        pm = MagicMock()
        mock_pm.return_value = pm

        llm_client = AsyncMock()

        from agents.content_agent.agents.creative_agent import CreativeAgent

        agent = CreativeAgent(llm_client=llm_client)
        agent.llm_client = llm_client
        agent.pm = pm
    return agent, llm_client, pm


# ---------------------------------------------------------------------------
# _extract_asset
# ---------------------------------------------------------------------------


class TestExtractAsset:
    def setup_method(self):
        self.agent, _, _ = make_agent()

    def test_extracts_title_from_text(self):
        text = "Title: My Amazing Article\nMeta: Something else"
        assert self.agent._extract_asset(text, "Title") == "My Amazing Article"

    def test_extracts_meta_description(self):
        text = "Title: Article\nMeta Description: A short summary of the article."
        assert (
            self.agent._extract_asset(text, "Meta Description") == "A short summary of the article."
        )

    def test_returns_empty_string_when_not_found(self):
        text = "Some random text without the label"
        assert self.agent._extract_asset(text, "Title") == ""

    def test_handles_extra_whitespace(self):
        text = "Title:    Spaced Out Title   \n"
        assert self.agent._extract_asset(text, "Title") == "Spaced Out Title"

    def test_handles_empty_string_input(self):
        assert self.agent._extract_asset("", "Title") == ""

    def test_multiline_matches_first_occurrence(self):
        text = "Title: First Title\nTitle: Second Title"
        result = self.agent._extract_asset(text, "Title")
        # re.search finds the first match
        assert result == "First Title"


# ---------------------------------------------------------------------------
# _clean_llm_output
# ---------------------------------------------------------------------------


class TestCleanLlmOutput:
    def setup_method(self):
        self.agent, _, _ = make_agent()

    def test_strips_preamble_before_first_heading(self):
        text = "Sure, here is your blog post:\n\n# The Real Title\n\nContent starts here."
        result = self.agent._clean_llm_output(text)
        assert result.startswith("# The Real Title")
        assert "Sure, here" not in result

    def test_returns_content_unchanged_when_starts_with_heading(self):
        text = "# My Article\n\nHello world."
        result = self.agent._clean_llm_output(text)
        assert result == text

    def test_handles_h2_heading(self):
        text = "Preamble line.\n## Section Heading\n\nBody text."
        result = self.agent._clean_llm_output(text)
        assert result.startswith("## Section Heading")

    def test_returns_empty_string_for_empty_input(self):
        assert self.agent._clean_llm_output("") == ""

    def test_returns_empty_string_for_none_input(self):
        # None should be handled gracefully
        assert self.agent._clean_llm_output(None) == ""  # type: ignore[arg-type]

    def test_adds_generic_heading_when_none_found(self):
        text = "This is just a paragraph.\nNo headings here at all."
        result = self.agent._clean_llm_output(text)
        # Should add a heading from the first reasonable line
        assert result.startswith("#")

    def test_fallback_heading_from_first_short_line(self):
        text = "Short line\nMore content here."
        result = self.agent._clean_llm_output(text)
        # First line becomes the heading
        assert "Short line" in result
        assert result.startswith("# Short line")

    def test_fallback_content_heading_when_first_line_too_long(self):
        long_line = "x" * 200
        text = f"{long_line}\nMore text."
        result = self.agent._clean_llm_output(text)
        # Long first line is skipped; "More text." becomes the heading
        assert result.startswith("# More text.")

    def test_skips_bullet_lines_for_heading_fallback(self):
        text = "- bullet item\nActual Title\nMore content."
        result = self.agent._clean_llm_output(text)
        # Should pick "Actual Title" not the bullet
        assert "Actual Title" in result

    def test_preserves_content_after_preamble_removal(self):
        text = "Generated for you:\n# Real Title\n\n## Subheading\n\nBody content."
        result = self.agent._clean_llm_output(text)
        assert "## Subheading" in result
        assert "Body content." in result


# ---------------------------------------------------------------------------
# run() — refinement guard logic (no real LLM calls)
# ---------------------------------------------------------------------------


class TestRunRefinementGuards:
    @pytest.mark.asyncio
    async def test_returns_post_early_when_max_refinements_exceeded(self):
        agent, llm_client, pm = make_agent()
        post = make_blog_post(
            qa_feedback=["r1", "r2", "r3", "r4"],  # 4 rounds
            refinement_loops=3,
            raw_content="# Existing content",
        )
        result = await agent.run(post, is_refinement=True)
        # Should not call LLM since max refinements exceeded
        llm_client.generate_text.assert_not_called()
        assert result is post

    @pytest.mark.asyncio
    async def test_returns_post_early_when_quality_score_above_threshold(self):
        agent, llm_client, pm = make_agent()
        post = make_blog_post(
            qa_feedback=["good"],
            quality_scores=[80.0, 85.0],
            raw_content="# Content",
        )
        result = await agent.run(post, is_refinement=True)
        llm_client.generate_text.assert_not_called()
        assert result is post

    @pytest.mark.asyncio
    async def test_returns_post_early_when_score_improvement_stalled(self):
        agent, llm_client, pm = make_agent()
        post = make_blog_post(
            qa_feedback=["r1", "r2"],
            quality_scores=[65.0, 66.0],  # improvement < 2.0
            raw_content="# Content",
        )
        result = await agent.run(post, is_refinement=True)
        llm_client.generate_text.assert_not_called()
        assert result is post

    @pytest.mark.asyncio
    async def test_proceeds_with_refinement_when_within_limits(self):
        agent, llm_client, pm = make_agent()
        post = make_blog_post(
            qa_feedback=["needs work"],
            quality_scores=[60.0],
            raw_content="# Draft content",
        )

        pm.get_prompt.return_value = "Refinement prompt text"
        llm_client.generate_text = AsyncMock(
            side_effect=["# Refined content", '{"title":"T","meta_description":"M"}']
        )

        result = await agent.run(post, is_refinement=True)
        # LLM was called (for refinement + SEO)
        assert llm_client.generate_text.call_count >= 1

    @pytest.mark.asyncio
    async def test_generates_initial_draft_when_not_refinement(self):
        agent, llm_client, pm = make_agent()
        post = make_blog_post()

        pm.get_prompt.return_value = "Draft prompt text"
        llm_client.generate_text = AsyncMock(
            side_effect=["# Initial content here", '{"title":"Test","meta_description":"Desc"}']
        )

        result = await agent.run(post, is_refinement=False)
        # Should have called llm for draft + SEO assets
        assert llm_client.generate_text.call_count == 2
        assert result.raw_content is not None and result.raw_content.startswith(
            "# Initial content here"
        )


# ---------------------------------------------------------------------------
# _generate_seo_assets() — fallback behavior
# ---------------------------------------------------------------------------


class TestGenerateSeoAssets:
    @pytest.mark.asyncio
    async def test_assigns_seo_fields_from_valid_json(self):
        agent, llm_client, pm = make_agent()
        pm.get_prompt.return_value = "SEO prompt"
        seo_json = json.dumps(
            {
                "title": "Great Article Title",
                "meta_description": "A concise description of the article.",
            }
        )
        llm_client.generate_text = AsyncMock(return_value=f"```json\n{seo_json}\n```")

        post = make_blog_post(raw_content="# Some content")
        result = await agent._generate_seo_assets(post)

        assert result.title == "Great Article Title"
        assert result.meta_description == "A concise description of the article."
        assert result.slug  # slug derived from title

    @pytest.mark.asyncio
    async def test_fallback_title_from_topic_when_no_json(self):
        agent, llm_client, pm = make_agent()
        pm.get_prompt.return_value = "SEO prompt"
        llm_client.generate_text = AsyncMock(return_value="No JSON in this response at all")

        post = make_blog_post(topic="Machine Learning Basics")
        result = await agent._generate_seo_assets(post)

        # Falls back to topic-derived title
        assert result.title  # not empty
        assert result.meta_description  # not empty
        assert result.slug  # not empty

    @pytest.mark.asyncio
    async def test_fallback_slug_when_title_empty(self):
        agent, llm_client, pm = make_agent()
        pm.get_prompt.return_value = "SEO prompt"
        # Return JSON with empty title
        llm_client.generate_text = AsyncMock(
            return_value='{"title": "", "meta_description": "Fine description"}'
        )

        post = make_blog_post(topic="Cloud Computing")
        result = await agent._generate_seo_assets(post)

        assert result.title  # fallback applied
        assert result.slug  # fallback slug generated

    @pytest.mark.asyncio
    async def test_handles_json_with_escaped_underscores(self):
        agent, llm_client, pm = make_agent()
        pm.get_prompt.return_value = "SEO prompt"
        # Simulate model output with escaped underscores
        escaped_json = '{"title": "Good Title", "meta\\_description": "Meta desc"}'
        llm_client.generate_text = AsyncMock(return_value=escaped_json)

        post = make_blog_post(raw_content="# Content")
        result = await agent._generate_seo_assets(post)
        # Should either parse or fallback gracefully
        assert result.title  # not empty regardless

    @pytest.mark.asyncio
    async def test_fallback_meta_description_under_160_chars(self):
        agent, llm_client, pm = make_agent()
        pm.get_prompt.return_value = "SEO prompt"
        llm_client.generate_text = AsyncMock(return_value="No JSON")

        post = make_blog_post(topic="A" * 200)  # Very long topic
        result = await agent._generate_seo_assets(post)

        assert result.meta_description is not None and len(result.meta_description) <= 160
