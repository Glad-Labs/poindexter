"""
Unit tests for agents/content_agent/agents/image_agent.py — ImageAgent

Tests focus on (all HTTP and filesystem I/O mocked):
- run(): early return when title/content missing
- run(): image processing loop — successful and partial failure paths
- _generate_image_metadata(): JSON extraction from LLM response, error handling
- _process_single_image(): download failure, upload via REST, missing query
- get_image_agent(): factory returns workflow-compatible adapter
"""

import json
import os
import pytest
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch, mock_open

os.environ.setdefault("DATABASE_URL", "postgresql://test:test@localhost:5432/test")
os.environ.setdefault("ANTHROPIC_API_KEY", "test-placeholder")


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def make_blog_post(**kwargs):
    from agents.content_agent.utils.data_models import BlogPost

    defaults = {
        "topic": "AI Trends",
        "primary_keyword": "artificial intelligence",
        "target_audience": "Tech professionals",
        "category": "Technology",
        "title": "The Future of AI",
        "raw_content": "# The Future of AI\n\nSome content here.",
    }
    defaults.update(kwargs)
    return BlogPost(**defaults)  # type: ignore[arg-type]


def make_agent(tmp_path=None):
    """Build an ImageAgent with all external dependencies mocked."""
    with (
        patch("agents.content_agent.agents.image_agent.CrewAIToolsFactory") as mock_tools,
        patch("agents.content_agent.agents.image_agent.config") as mock_cfg,
        patch("agents.content_agent.agents.image_agent.load_prompts_from_file") as mock_prompts,
        patch("os.makedirs"),
    ):
        mock_tools.get_content_agent_tools.return_value = []
        mock_cfg.DEFAULT_IMAGE_PLACEHOLDERS = 3
        mock_cfg.IMAGE_STORAGE_PATH = str(tmp_path) if tmp_path else "/tmp/test_images"
        mock_prompts.return_value = {
            "image_metadata_generation": "Generate {num_images} images for title: {title}"
        }

        llm_client = AsyncMock()
        pexels_client = AsyncMock()
        strapi_client = AsyncMock()

        from agents.content_agent.agents.image_agent import ImageAgent

        agent = ImageAgent(
            llm_client=llm_client,
            pexels_client=pexels_client,
            strapi_client=strapi_client,
            api_url="http://localhost:8000",
        )
        agent.llm_client = llm_client
        agent.pexels_client = pexels_client
        agent.strapi_client = strapi_client
        agent.prompts = {
            "image_metadata_generation": "Generate {num_images} images for title: {title}"
        }

    return agent, llm_client, pexels_client, strapi_client


# ---------------------------------------------------------------------------
# run() — early returns
# ---------------------------------------------------------------------------


class TestImageAgentRun:
    @pytest.mark.asyncio
    async def test_returns_post_unchanged_when_title_missing(self, tmp_path):
        agent, llm_client, pexels_client, _ = make_agent(tmp_path)
        post = make_blog_post(title="")

        result = await agent.run(post)

        llm_client.generate_text.assert_not_called()
        assert result is post

    @pytest.mark.asyncio
    async def test_returns_post_unchanged_when_raw_content_missing(self, tmp_path):
        agent, llm_client, pexels_client, _ = make_agent(tmp_path)
        post = make_blog_post(raw_content="")

        result = await agent.run(post)

        llm_client.generate_text.assert_not_called()
        assert result is post

    @pytest.mark.asyncio
    async def test_returns_post_unchanged_when_no_metadata_generated(self, tmp_path):
        """LLM returns non-JSON — no images added."""
        agent, llm_client, pexels_client, _ = make_agent(tmp_path)
        llm_client.generate_text = AsyncMock(return_value="not json at all")
        post = make_blog_post()

        result = await agent.run(post)

        assert result.images is None or result.images == []

    @pytest.mark.asyncio
    async def test_appends_image_details_on_successful_download(self, tmp_path):
        """Patches _generate_image_metadata to isolate the image-processing loop."""
        agent, llm_client, pexels_client, strapi_client = make_agent(tmp_path)
        pexels_client.search_and_download = AsyncMock(return_value=True)
        strapi_client.upload_image = AsyncMock(return_value=42)

        mock_upload_resp = MagicMock()
        mock_upload_resp.raise_for_status = MagicMock()
        mock_upload_resp.json.return_value = {"url": "http://cdn.example.com/image.jpg"}

        metadata_list = [{"query": "AI robot", "alt_text": "Robot image", "caption": "AI Robot"}]
        post = make_blog_post()

        with (
            patch.object(
                agent, "_generate_image_metadata", new=AsyncMock(return_value=metadata_list)
            ),
            patch("agents.content_agent.agents.image_agent.httpx.AsyncClient") as mock_cls,
            patch("builtins.open", mock_open(read_data=b"fake-image-bytes")),
        ):
            mock_http = AsyncMock()
            mock_http.post = AsyncMock(return_value=mock_upload_resp)
            mock_cls.return_value.__aenter__ = AsyncMock(return_value=mock_http)
            mock_cls.return_value.__aexit__ = AsyncMock(return_value=None)

            result = await agent.run(post)

        assert result.images is not None
        assert len(result.images) == 1
        assert result.images[0].query == "AI robot"
        assert result.images[0].strapi_image_id == 42

    @pytest.mark.asyncio
    async def test_skips_image_when_download_fails(self, tmp_path):
        agent, llm_client, pexels_client, strapi_client = make_agent(tmp_path)
        pexels_client.search_and_download = AsyncMock(return_value=False)

        metadata_list = [{"query": "sunset", "alt_text": "Sunset", "caption": "A sunset"}]
        post = make_blog_post()

        with patch.object(
            agent, "_generate_image_metadata", new=AsyncMock(return_value=metadata_list)
        ):
            result = await agent.run(post)

        # No images should be added
        assert not result.images


# ---------------------------------------------------------------------------
# _generate_image_metadata
# ---------------------------------------------------------------------------


class TestGenerateImageMetadata:
    @pytest.mark.asyncio
    async def test_returns_dict_when_llm_returns_json_array(self, tmp_path):
        """extract_json_from_string extracts first {..} from the array, returning a dict.
        The function returns that dict — callers must handle this."""
        agent, llm_client, _, _ = make_agent(tmp_path)
        data = [{"query": "mountain", "alt_text": "Mountain"}]
        llm_client.generate_text = AsyncMock(return_value=json.dumps(data))
        post = make_blog_post()

        result = await agent._generate_image_metadata(post)

        # extract_json_from_string pulls out the first dict from the array
        assert result == {"query": "mountain", "alt_text": "Mountain"}

    @pytest.mark.asyncio
    async def test_parses_bare_json_array_when_no_objects_extracted(self, tmp_path):
        """When text is a bare array (no {} pattern), extract returns None and
        json.loads(metadata_text) is used, correctly returning a list."""
        agent, llm_client, _, _ = make_agent(tmp_path)
        # Use a JSON array with non-object first chars to force the fallback
        # Simpler: patch extract_json_from_string to return None
        data = [{"query": "city skyline", "alt_text": "Skyline"}]
        raw_json = json.dumps(data)
        llm_client.generate_text = AsyncMock(return_value=raw_json)
        post = make_blog_post()

        with patch(
            "agents.content_agent.agents.image_agent.extract_json_from_string",
            return_value=None,
        ):
            result = await agent._generate_image_metadata(post)

        assert result == data

    @pytest.mark.asyncio
    async def test_returns_empty_list_on_invalid_json(self, tmp_path):
        agent, llm_client, _, _ = make_agent(tmp_path)
        llm_client.generate_text = AsyncMock(return_value="not valid json at all")
        post = make_blog_post()

        result = await agent._generate_image_metadata(post)

        assert result == []

    @pytest.mark.asyncio
    async def test_run_returns_post_when_llm_raises(self, tmp_path):
        """RuntimeError from LLM propagates up to run() which catches it."""
        agent, llm_client, _, _ = make_agent(tmp_path)
        llm_client.generate_text = AsyncMock(side_effect=RuntimeError("LLM error"))
        post = make_blog_post()

        result = await agent.run(post)

        # run() handles the exception — post should still be returned
        assert result is post


# ---------------------------------------------------------------------------
# _process_single_image
# ---------------------------------------------------------------------------


class TestProcessSingleImage:
    @pytest.mark.asyncio
    async def test_returns_none_when_query_missing(self, tmp_path):
        agent, _, pexels_client, _ = make_agent(tmp_path)
        post = make_blog_post()

        result = await agent._process_single_image({}, post, 0)

        assert result is None
        pexels_client.search_and_download.assert_not_called()

    @pytest.mark.asyncio
    async def test_returns_none_when_title_missing(self, tmp_path):
        agent, _, pexels_client, _ = make_agent(tmp_path)
        post = make_blog_post(title="")

        result = await agent._process_single_image(
            {"query": "mountains", "alt_text": "Alt"}, post, 0
        )

        assert result is None

    @pytest.mark.asyncio
    async def test_returns_none_when_pexels_download_fails(self, tmp_path):
        agent, _, pexels_client, _ = make_agent(tmp_path)
        pexels_client.search_and_download = AsyncMock(return_value=False)
        post = make_blog_post()

        result = await agent._process_single_image(
            {"query": "sky", "alt_text": "Sky", "caption": "Blue sky"},
            post,
            0,
        )

        assert result is None

    @pytest.mark.asyncio
    async def test_falls_back_to_local_path_when_rest_upload_fails(self, tmp_path):
        agent, _, pexels_client, strapi_client = make_agent(tmp_path)
        pexels_client.search_and_download = AsyncMock(return_value=True)
        strapi_client.upload_image = AsyncMock(return_value=None)

        with (
            patch("agents.content_agent.agents.image_agent.httpx.AsyncClient") as mock_cls,
            patch("builtins.open", mock_open(read_data=b"img")),
        ):
            mock_http = AsyncMock()
            mock_http.post = AsyncMock(side_effect=RuntimeError("REST API down"))
            mock_cls.return_value.__aenter__ = AsyncMock(return_value=mock_http)
            mock_cls.return_value.__aexit__ = AsyncMock(return_value=None)

            post = make_blog_post()
            result = await agent._process_single_image(
                {"query": "forest", "alt_text": "Forest", "caption": "Green forest"},
                post,
                0,
            )

        # Falls back to local path — should return ImageDetails with local path as public_url
        assert result is not None
        assert result.query == "forest"


# ---------------------------------------------------------------------------
# get_image_agent factory
# ---------------------------------------------------------------------------


class TestGetImageAgentFactory:
    def test_returns_workflow_compatible_agent(self):
        """get_image_agent delegates to get_blog_image_agent via a local import.
        We patch the blog_image_agent module directly."""
        mock_blog_agent = MagicMock()
        mock_blog_agent_module = MagicMock()
        mock_blog_agent_module.get_blog_image_agent.return_value = mock_blog_agent

        with patch.dict(
            "sys.modules", {"agents.blog_image_agent": mock_blog_agent_module}
        ):
            from agents.content_agent.agents.image_agent import get_image_agent

            agent = get_image_agent()
            mock_blog_agent_module.get_blog_image_agent.assert_called_once()
            assert agent is mock_blog_agent
