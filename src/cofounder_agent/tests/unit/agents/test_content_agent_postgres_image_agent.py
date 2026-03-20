"""
Unit tests for agents/content_agent/agents/postgres_image_agent.py

Tests for PostgreSQLImageAgent class.
"""

import pytest
from unittest.mock import AsyncMock, MagicMock, patch

from agents.content_agent.agents.postgres_image_agent import PostgreSQLImageAgent
from agents.content_agent.utils.data_models import BlogPost, ImageDetails


# ---------------------------------------------------------------------------
# Defaults / helpers
# ---------------------------------------------------------------------------

DEFAULTS = {  # type: ignore[arg-type]
    "topic": "AI Trends",
    "primary_keyword": "artificial intelligence",
    "target_audience": "developers",
    "category": "tech",
}


def _make_post(**kwargs) -> BlogPost:
    data = {**DEFAULTS, **kwargs}
    return BlogPost(**data)  # type: ignore[arg-type]


def _make_agent():
    mock_llm = AsyncMock()
    mock_llm.generate_text = AsyncMock(
        return_value='[{"query": "AI technology", "alt_text": "AI tech image"}]'
    )
    mock_pexels = AsyncMock()

    with patch("agents.content_agent.agents.postgres_image_agent.config") as mock_cfg, \
         patch("agents.content_agent.agents.postgres_image_agent.load_prompts_from_file", return_value={}), \
         patch("agents.content_agent.agents.postgres_image_agent.Path") as mock_path:
        mock_cfg.PROMPTS_PATH = "/fake/prompts.json"
        mock_cfg.IMAGE_STORAGE_PATH = "/tmp/images"
        mock_cfg.DEFAULT_IMAGE_PLACEHOLDERS = 3
        mock_path_instance = MagicMock()
        mock_path.return_value = mock_path_instance
        mock_path_instance.mkdir = MagicMock()

        agent = PostgreSQLImageAgent(
            llm_client=mock_llm,
            pexels_client=mock_pexels,
            api_url="http://localhost:8000",
        )

    return agent, mock_llm, mock_pexels


# ---------------------------------------------------------------------------
# __init__
# ---------------------------------------------------------------------------


class TestPostgreSQLImageAgentInit:
    def test_stores_llm_client(self):
        agent, mock_llm, _ = _make_agent()
        assert agent.llm_client is mock_llm

    def test_stores_pexels_client(self):
        agent, _, mock_pexels = _make_agent()
        assert agent.pexels_client is mock_pexels

    def test_stores_api_url(self):
        agent, _, _ = _make_agent()
        assert agent.api_url == "http://localhost:8000"

    def test_prompts_loaded_or_defaulted(self):
        agent, _, _ = _make_agent()
        assert isinstance(agent.prompts, dict)


# ---------------------------------------------------------------------------
# run — missing title/content guard
# ---------------------------------------------------------------------------


class TestPostgreSQLImageAgentRun:
    @pytest.mark.asyncio
    async def test_skips_when_no_title(self):
        agent, mock_llm, _ = _make_agent()
        post = _make_post()
        post.title = None
        post.raw_content = None

        result = await agent.run(post)
        assert result is post
        mock_llm.generate_text.assert_not_called()

    @pytest.mark.asyncio
    async def test_skips_when_no_raw_content(self):
        agent, mock_llm, _ = _make_agent()
        post = _make_post(title="My Post")
        post.raw_content = None

        result = await agent.run(post)
        assert result is post
        mock_llm.generate_text.assert_not_called()

    @pytest.mark.asyncio
    async def test_returns_post_when_no_metadata_generated(self):
        agent, mock_llm, _ = _make_agent()
        mock_llm.generate_text = AsyncMock(return_value="not json")
        post = _make_post(title="My Post", raw_content="Content here")

        result = await agent.run(post)
        assert result is post

    @pytest.mark.asyncio
    async def test_continues_on_image_processing_error(self):
        agent, mock_llm, mock_pexels = _make_agent()
        mock_pexels.search_images = AsyncMock(side_effect=RuntimeError("pexels down"))
        post = _make_post(title="My Post", raw_content="Content here")

        # Should not raise — exception should be caught internally
        result = await agent.run(post)
        assert result is not None

    @pytest.mark.asyncio
    async def test_returns_post_object(self):
        agent, mock_llm, mock_pexels = _make_agent()
        mock_pexels.search_images = AsyncMock(return_value=[])
        post = _make_post(title="My Post", raw_content="Content here")

        result = await agent.run(post)
        assert isinstance(result, BlogPost)


# ---------------------------------------------------------------------------
# _generate_image_metadata
# ---------------------------------------------------------------------------


class TestGenerateImageMetadata:
    @pytest.mark.asyncio
    async def test_returns_list_on_valid_json(self):
        agent, mock_llm, _ = _make_agent()
        mock_llm.generate_text = AsyncMock(
            return_value='[{"query": "tech", "alt_text": "tech image"}]'
        )
        post = _make_post(title="Tech Post", raw_content="Content")

        result = await agent._generate_image_metadata(post)
        assert isinstance(result, list)
        assert len(result) == 1
        assert result[0]["query"] == "tech"

    @pytest.mark.asyncio
    async def test_returns_empty_list_on_empty_llm_response(self):
        agent, mock_llm, _ = _make_agent()
        mock_llm.generate_text = AsyncMock(return_value="")
        post = _make_post(title="Post", raw_content="Content")

        result = await agent._generate_image_metadata(post)
        assert result == []

    @pytest.mark.asyncio
    async def test_returns_empty_list_on_invalid_json(self):
        agent, mock_llm, _ = _make_agent()
        mock_llm.generate_text = AsyncMock(return_value="this is not json at all")
        post = _make_post(title="Post", raw_content="Content")

        result = await agent._generate_image_metadata(post)
        assert result == []

    @pytest.mark.asyncio
    async def test_uses_prompt_template_when_available(self):
        agent, mock_llm, _ = _make_agent()
        agent.prompts = {
            "image_metadata_generation": "Generate {num_images} images for: {title}"
        }
        mock_llm.generate_text = AsyncMock(return_value='[]')
        post = _make_post(title="Test", raw_content="Content")

        await agent._generate_image_metadata(post)
        call_args = mock_llm.generate_text.call_args[0][0]
        assert "Test" in call_args

    @pytest.mark.asyncio
    async def test_handles_dict_response(self):
        agent, mock_llm, _ = _make_agent()
        mock_llm.generate_text = AsyncMock(
            return_value='{"query": "nature", "alt_text": "nature scene"}'
        )
        post = _make_post(title="Post", raw_content="Content")

        result = await agent._generate_image_metadata(post)
        # Single dict wrapped in list or returned as-is depending on implementation
        assert isinstance(result, list)


# ---------------------------------------------------------------------------
# _process_single_image_async
# ---------------------------------------------------------------------------


class TestProcessSingleImageAsync:
    @pytest.mark.asyncio
    async def test_returns_none_for_empty_query(self):
        agent, _, mock_pexels = _make_agent()
        post = _make_post(title="Post", raw_content="Content")
        meta = {"query": "", "alt_text": "alt"}

        result = await agent._process_single_image_async(meta, post, 0)
        assert result is None
        mock_pexels.search_images.assert_not_called()

    @pytest.mark.asyncio
    async def test_returns_image_details_on_success(self):
        agent, _, mock_pexels = _make_agent()
        mock_pexels.search_images = AsyncMock(
            return_value=[{"url": "https://pexels.com/photo/1.jpg", "photographer": "John"}]
        )
        post = _make_post(title="Post", raw_content="Content")
        meta = {"query": "sunset", "alt_text": "beautiful sunset"}

        result = await agent._process_single_image_async(meta, post, 0)
        assert result is not None
        assert isinstance(result, ImageDetails)
        assert result.query == "sunset"
        assert result.source == "pexels"

    @pytest.mark.asyncio
    async def test_returns_none_when_no_pexels_results(self):
        agent, _, mock_pexels = _make_agent()
        mock_pexels.search_images = AsyncMock(return_value=[])
        post = _make_post(title="Post", raw_content="Content")
        meta = {"query": "rare obscure thing", "alt_text": "alt"}

        result = await agent._process_single_image_async(meta, post, 0)
        assert result is None

    @pytest.mark.asyncio
    async def test_returns_none_on_pexels_exception(self):
        agent, _, mock_pexels = _make_agent()
        mock_pexels.search_images = AsyncMock(side_effect=RuntimeError("API down"))
        post = _make_post(title="Post", raw_content="Content")
        meta = {"query": "query", "alt_text": "alt"}

        result = await agent._process_single_image_async(meta, post, 0)
        assert result is None

    @pytest.mark.asyncio
    async def test_image_details_has_alt_text(self):
        agent, _, mock_pexels = _make_agent()
        mock_pexels.search_images = AsyncMock(
            return_value=[{"url": "https://img.jpg", "photographer": "Jane"}]
        )
        post = _make_post(title="Post", raw_content="Content")
        meta = {"query": "mountains", "alt_text": "snowy mountains"}

        result = await agent._process_single_image_async(meta, post, 0)
        assert result is not None
        assert result.alt_text == "snowy mountains"
