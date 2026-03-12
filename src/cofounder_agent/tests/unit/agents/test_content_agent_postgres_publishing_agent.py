"""
Unit tests for agents/content_agent/agents/postgres_publishing_agent.py

Tests for PostgreSQLPublishingAgent class.
"""

import pytest
from unittest.mock import AsyncMock, MagicMock, patch

from agents.content_agent.agents.postgres_publishing_agent import PostgreSQLPublishingAgent
from agents.content_agent.utils.data_models import BlogPost


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

DEFAULTS = {  # type: ignore[arg-type]
    "topic": "AI Trends",
    "primary_keyword": "artificial intelligence",
    "target_audience": "developers",
    "category": "tech",
}


def _make_post(**kwargs) -> BlogPost:
    return BlogPost(**{**DEFAULTS, **kwargs})  # type: ignore[arg-type]


def _make_agent(cms_client=None) -> PostgreSQLPublishingAgent:
    if cms_client is None:
        cms_client = AsyncMock()
    with patch(
        "agents.content_agent.agents.postgres_publishing_agent.PostgresCMSClient",
        return_value=cms_client,
    ):
        agent = PostgreSQLPublishingAgent(cms_client=cms_client)
    return agent


# ---------------------------------------------------------------------------
# __init__
# ---------------------------------------------------------------------------


class TestPostgreSQLPublishingAgentInit:
    def test_stores_cms_client(self):
        mock_cms = AsyncMock()
        agent = _make_agent(cms_client=mock_cms)
        assert agent.cms_client is mock_cms

    def test_creates_default_cms_client_when_none(self):
        with patch(
            "agents.content_agent.agents.postgres_publishing_agent.PostgresCMSClient"
        ) as mock_cls:
            mock_instance = AsyncMock()
            mock_cls.return_value = mock_instance
            agent = PostgreSQLPublishingAgent()
        assert agent.cms_client is mock_instance

    def test_logs_info_on_init(self):
        with patch(
            "agents.content_agent.agents.postgres_publishing_agent.logger"
        ) as mock_logger, patch(
            "agents.content_agent.agents.postgres_publishing_agent.PostgresCMSClient"
        ):
            PostgreSQLPublishingAgent()
            mock_logger.info.assert_called()


# ---------------------------------------------------------------------------
# initialize
# ---------------------------------------------------------------------------


class TestInitialize:
    @pytest.mark.asyncio
    async def test_calls_initialize_when_no_pool(self):
        mock_cms = AsyncMock()
        mock_cms.initialize = AsyncMock()
        # Simulate no pool attribute
        del mock_cms.pool
        agent = _make_agent(cms_client=mock_cms)

        await agent.initialize()
        mock_cms.initialize.assert_called_once()

    @pytest.mark.asyncio
    async def test_skips_initialize_when_pool_exists(self):
        mock_cms = AsyncMock()
        mock_cms.pool = MagicMock()  # pool already set
        mock_cms.initialize = AsyncMock()
        agent = _make_agent(cms_client=mock_cms)

        await agent.initialize()
        mock_cms.initialize.assert_not_called()


# ---------------------------------------------------------------------------
# run — validation and slug generation
# ---------------------------------------------------------------------------


class TestRun:
    @pytest.mark.asyncio
    async def test_raises_on_missing_raw_content(self):
        agent = _make_agent()
        post = _make_post()
        post.raw_content = None

        with pytest.raises(ValueError, match="raw_content"):
            await agent.run(post)

    @pytest.mark.asyncio
    async def test_generates_slug_from_title(self):
        agent = _make_agent()
        post = _make_post(title="My Amazing Post!", raw_content="Content here")
        post.slug = None

        result = await agent.run(post)
        assert result.slug is not None
        assert len(result.slug) > 0

    @pytest.mark.asyncio
    async def test_preserves_existing_slug(self):
        agent = _make_agent()
        post = _make_post(title="My Post", raw_content="Content", slug="existing-slug")

        result = await agent.run(post)
        assert result.slug == "existing-slug"

    @pytest.mark.asyncio
    async def test_sets_meta_description_when_missing(self):
        agent = _make_agent()
        post = _make_post(title="Post", raw_content="A" * 300)
        post.meta_description = None

        result = await agent.run(post)
        assert result.meta_description is not None
        assert len(result.meta_description) > 0

    @pytest.mark.asyncio
    async def test_preserves_existing_meta_description(self):
        agent = _make_agent()
        post = _make_post(
            title="Post", raw_content="Content", meta_description="Custom meta"
        )

        result = await agent.run(post)
        assert result.meta_description == "Custom meta"

    @pytest.mark.asyncio
    async def test_sets_strapi_url_from_slug(self):
        agent = _make_agent()
        post = _make_post(title="My Post", raw_content="Content", slug="my-post")

        result = await agent.run(post)
        assert result.strapi_url == "/posts/my-post"

    @pytest.mark.asyncio
    async def test_strapi_id_set_to_none(self):
        agent = _make_agent()
        post = _make_post(title="Post", raw_content="Content")

        result = await agent.run(post)
        assert result.strapi_id is None

    @pytest.mark.asyncio
    async def test_returns_blog_post_object(self):
        agent = _make_agent()
        post = _make_post(title="Post", raw_content="Content")

        result = await agent.run(post)
        assert isinstance(result, BlogPost)

    @pytest.mark.asyncio
    async def test_slug_lowercased_and_hyphenated(self):
        agent = _make_agent()
        post = _make_post(title="Hello World Post", raw_content="Content")
        post.slug = None

        result = await agent.run(post)
        assert result.slug is not None and " " not in result.slug
        assert result.slug == result.slug.lower()

    @pytest.mark.asyncio
    async def test_short_content_no_ellipsis(self):
        agent = _make_agent()
        post = _make_post(title="Post", raw_content="Short content")
        post.meta_description = None

        result = await agent.run(post)
        # Content is short so no ellipsis should be added
        assert result.meta_description is not None and "..." not in result.meta_description


# ---------------------------------------------------------------------------
# run_async
# ---------------------------------------------------------------------------


class TestRunAsync:
    @pytest.mark.asyncio
    async def test_returns_post_id_and_slug(self):
        mock_cms = AsyncMock()
        mock_cms.pool = MagicMock()
        mock_cms.create_post = AsyncMock(return_value=("uuid-123", "my-post"))
        agent = _make_agent(cms_client=mock_cms)

        post = _make_post(title="My Post", raw_content="Content", slug="my-post")
        post_id, slug = await agent.run_async(post)

        assert post_id == "uuid-123"
        assert slug == "my-post"

    @pytest.mark.asyncio
    async def test_calls_cms_create_post(self):
        mock_cms = AsyncMock()
        mock_cms.pool = MagicMock()
        mock_cms.create_post = AsyncMock(return_value=("id-456", "slug-456"))
        agent = _make_agent(cms_client=mock_cms)

        post = _make_post(title="Post", raw_content="Content")
        await agent.run_async(post)
        mock_cms.create_post.assert_called_once_with(post)

    @pytest.mark.asyncio
    async def test_propagates_exception_from_cms(self):
        mock_cms = AsyncMock()
        mock_cms.pool = MagicMock()
        mock_cms.create_post = AsyncMock(side_effect=RuntimeError("DB error"))
        agent = _make_agent(cms_client=mock_cms)

        post = _make_post(title="Post", raw_content="Content")
        with pytest.raises(RuntimeError, match="DB error"):
            await agent.run_async(post)


# ---------------------------------------------------------------------------
# close
# ---------------------------------------------------------------------------


class TestClose:
    @pytest.mark.asyncio
    async def test_calls_cms_close(self):
        mock_cms = AsyncMock()
        mock_cms.close = AsyncMock()
        agent = _make_agent(cms_client=mock_cms)

        await agent.close()
        mock_cms.close.assert_called_once()

    @pytest.mark.asyncio
    async def test_no_error_when_no_close_method(self):
        mock_cms = MagicMock(spec=[])  # No close method
        agent = _make_agent(cms_client=mock_cms)

        # Should not raise
        await agent.close()
