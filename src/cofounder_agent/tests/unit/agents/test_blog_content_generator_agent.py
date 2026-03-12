"""
Unit tests for agents/blog_content_generator_agent.py — BlogContentGeneratorAgent
"""

import pytest
from unittest.mock import AsyncMock, MagicMock, patch
from agents.blog_content_generator_agent import (
    BlogContentGeneratorAgent,
    get_blog_content_generator_agent,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def make_agent(content_generator=None):
    """Build an agent with mocked content generator, bypassing __init__ imports."""
    with patch("agents.blog_content_generator_agent.get_content_generator") as mock_factory:
        mock_gen = content_generator or AsyncMock()
        mock_factory.return_value = mock_gen
        agent = BlogContentGeneratorAgent()
        agent.content_generator = mock_gen
    return agent, mock_gen


# ---------------------------------------------------------------------------
# run() — success paths
# ---------------------------------------------------------------------------


class TestRunSuccess:
    @pytest.mark.asyncio
    async def test_returns_success_status_on_happy_path(self):
        agent, mock_gen = make_agent()
        mock_gen.generate_blog_post = AsyncMock(
            return_value=("Hello world content", "gpt-4", {"tokens": 100})
        )
        result = await agent.run({"topic": "AI in healthcare"})
        assert result["status"] == "success"
        assert result["content"] == "Hello world content"
        assert result["model_used"] == "gpt-4"
        assert result["metrics"] == {"tokens": 100}
        assert result["word_count"] == 3  # "Hello world content"

    @pytest.mark.asyncio
    async def test_passes_correct_parameters_to_generator(self):
        agent, mock_gen = make_agent()
        mock_gen.generate_blog_post = AsyncMock(
            return_value=("content text here", "claude-3", {})
        )
        await agent.run(
            {
                "topic": "Machine learning trends",
                "style": "technical",
                "tone": "academic",
                "target_length": 2000,
                "tags": ["ml", "ai"],
                "preferred_model": "claude-3",
                "preferred_provider": "anthropic",
            }
        )
        mock_gen.generate_blog_post.assert_awaited_once_with(
            topic="Machine learning trends",
            style="technical",
            tone="academic",
            target_length=2000,
            tags=["ml", "ai"],
            preferred_model="claude-3",
            preferred_provider="anthropic",
        )

    @pytest.mark.asyncio
    async def test_uses_defaults_when_optional_params_missing(self):
        agent, mock_gen = make_agent()
        mock_gen.generate_blog_post = AsyncMock(
            return_value=("content here now", "model", {})
        )
        await agent.run({"topic": "Python programming"})
        call_kwargs = mock_gen.generate_blog_post.call_args.kwargs
        assert call_kwargs["style"] == "balanced"
        assert call_kwargs["tone"] == "professional"
        assert call_kwargs["target_length"] == 1500
        assert call_kwargs["tags"] == ["Python programming"]  # defaults to [topic]
        assert call_kwargs["preferred_model"] is None
        assert call_kwargs["preferred_provider"] is None

    @pytest.mark.asyncio
    async def test_word_count_matches_content(self):
        agent, mock_gen = make_agent()
        content = "one two three four five"
        mock_gen.generate_blog_post = AsyncMock(return_value=(content, "m", {}))
        result = await agent.run({"topic": "test topic"})
        assert result["word_count"] == 5


# ---------------------------------------------------------------------------
# run() — validation errors
# ---------------------------------------------------------------------------


class TestRunValidation:
    @pytest.mark.asyncio
    async def test_empty_topic_returns_failed(self):
        agent, _ = make_agent()
        result = await agent.run({"topic": ""})
        assert result["status"] == "failed"
        assert "Topic" in result["error"]
        assert result["content"] is None

    @pytest.mark.asyncio
    async def test_short_topic_returns_failed(self):
        agent, _ = make_agent()
        result = await agent.run({"topic": "ab"})
        assert result["status"] == "failed"
        assert result["content"] is None

    @pytest.mark.asyncio
    async def test_whitespace_only_topic_returns_failed(self):
        agent, _ = make_agent()
        result = await agent.run({"topic": "   "})
        assert result["status"] == "failed"

    @pytest.mark.asyncio
    async def test_missing_topic_returns_failed(self):
        agent, _ = make_agent()
        result = await agent.run({})
        assert result["status"] == "failed"


# ---------------------------------------------------------------------------
# run() — error handling
# ---------------------------------------------------------------------------


class TestRunErrorHandling:
    @pytest.mark.asyncio
    async def test_generator_exception_returns_failed(self):
        agent, mock_gen = make_agent()
        mock_gen.generate_blog_post = AsyncMock(side_effect=RuntimeError("LLM unavailable"))
        result = await agent.run({"topic": "test topic here"})
        assert result["status"] == "failed"
        assert "LLM unavailable" in result["error"]
        assert result["content"] is None

    @pytest.mark.asyncio
    async def test_network_error_returns_failed(self):
        agent, mock_gen = make_agent()
        mock_gen.generate_blog_post = AsyncMock(
            side_effect=ConnectionError("Connection refused")
        )
        result = await agent.run({"topic": "test topic here"})
        assert result["status"] == "failed"


# ---------------------------------------------------------------------------
# Factory function
# ---------------------------------------------------------------------------


class TestFactory:
    def test_get_blog_content_generator_agent_returns_instance(self):
        with patch("agents.blog_content_generator_agent.get_content_generator"):
            agent = get_blog_content_generator_agent()
        assert isinstance(agent, BlogContentGeneratorAgent)
