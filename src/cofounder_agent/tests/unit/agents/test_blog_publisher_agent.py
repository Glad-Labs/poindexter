"""
Unit tests for agents/blog_publisher_agent.py — BlogPublisherAgent
"""

import pytest
from unittest.mock import AsyncMock, MagicMock, patch
from agents.blog_publisher_agent import BlogPublisherAgent, get_blog_publisher_agent

LONG_CONTENT = "word " * 50  # 250 chars


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def make_agent(db_service=None):
    """Build a BlogPublisherAgent with an injected mock DatabaseService."""
    mock_db = db_service or AsyncMock()
    mock_db.create_post = AsyncMock(return_value={"id": "post-abc-123"})
    agent = BlogPublisherAgent(database_service=mock_db)
    return agent, mock_db


# ---------------------------------------------------------------------------
# run() — success paths
# ---------------------------------------------------------------------------


class TestRunSuccess:
    @pytest.mark.asyncio
    async def test_returns_success_status(self):
        agent, db = make_agent()
        result = await agent.run({"content": LONG_CONTENT, "topic": "AI trends"})
        assert result["status"] == "success"
        assert result["post_id"] == "post-abc-123"

    @pytest.mark.asyncio
    async def test_slug_derived_from_title(self):
        agent, db = make_agent()
        result = await agent.run({"content": LONG_CONTENT, "title": "Hello World Post"})
        assert result["slug"] == "hello-world-post"
        assert result["url"] == "/posts/hello-world-post"

    @pytest.mark.asyncio
    async def test_slug_derived_from_topic_when_no_title(self):
        agent, db = make_agent()
        result = await agent.run({"content": LONG_CONTENT, "topic": "My Blog Topic"})
        assert "my-blog-topic" in result["slug"]

    @pytest.mark.asyncio
    async def test_title_returned_in_result(self):
        agent, db = make_agent()
        result = await agent.run({"content": LONG_CONTENT, "title": "My Title"})
        assert result["title"] == "My Title"

    @pytest.mark.asyncio
    async def test_featured_image_dict_included_in_post_data(self):
        agent, db = make_agent()
        featured = {
            "url": "https://img.example.com/photo.jpg",
            "alt_text": "A photo",
            "photographer": "Jane Doe",
        }
        await agent.run({"content": LONG_CONTENT, "featured_image": featured})
        call_args = db.create_post.call_args[0][0]
        assert call_args["featured_image_url"] == "https://img.example.com/photo.jpg"
        assert call_args["featured_image_alt"] == "A photo"
        assert call_args["featured_image_photographer"] == "Jane Doe"

    @pytest.mark.asyncio
    async def test_featured_image_as_url_string(self):
        agent, db = make_agent()
        await agent.run({"content": LONG_CONTENT, "featured_image": "https://direct-url.com"})
        call_args = db.create_post.call_args[0][0]
        assert call_args["featured_image_url"] == "https://direct-url.com"

    @pytest.mark.asyncio
    async def test_tags_joined_as_seo_keywords(self):
        agent, db = make_agent()
        await agent.run({"content": LONG_CONTENT, "tags": ["python", "ai", "ml"]})
        call_args = db.create_post.call_args[0][0]
        assert call_args["seo_keywords"] == "python, ai, ml"

    @pytest.mark.asyncio
    async def test_tags_as_string_stored_directly(self):
        agent, db = make_agent()
        await agent.run({"content": LONG_CONTENT, "tags": "python ai"})
        call_args = db.create_post.call_args[0][0]
        assert call_args["seo_keywords"] == "python ai"

    @pytest.mark.asyncio
    async def test_publish_true_sets_published_status(self):
        agent, db = make_agent()
        await agent.run({"content": LONG_CONTENT, "publish": True})
        call_args = db.create_post.call_args[0][0]
        assert call_args["status"] == "published"
        assert call_args["published"] is True

    @pytest.mark.asyncio
    async def test_publish_false_sets_draft_status(self):
        agent, db = make_agent()
        await agent.run({"content": LONG_CONTENT, "publish": False})
        call_args = db.create_post.call_args[0][0]
        assert call_args["status"] == "draft"
        assert call_args["published"] is False

    @pytest.mark.asyncio
    async def test_seo_description_truncated_to_160_chars(self):
        agent, db = make_agent()
        content = "x" * 300
        await agent.run({"content": content})
        call_args = db.create_post.call_args[0][0]
        assert len(call_args["seo_description"]) == 160

    @pytest.mark.asyncio
    async def test_create_post_called_with_dict(self):
        agent, db = make_agent()
        await agent.run({"content": LONG_CONTENT})
        db.create_post.assert_awaited_once()
        arg = db.create_post.call_args[0][0]
        assert isinstance(arg, dict)
        assert "id" in arg

    @pytest.mark.asyncio
    async def test_result_post_id_from_create_post_string(self):
        agent, db = make_agent()
        db.create_post = AsyncMock(return_value="simple-string-id")
        result = await agent.run({"content": LONG_CONTENT})
        assert result["post_id"] == "simple-string-id"


# ---------------------------------------------------------------------------
# run() — validation errors
# ---------------------------------------------------------------------------


class TestRunValidation:
    @pytest.mark.asyncio
    async def test_empty_content_returns_failed(self):
        agent, _ = make_agent()
        result = await agent.run({"content": ""})
        assert result["status"] == "failed"
        assert result["post_id"] is None

    @pytest.mark.asyncio
    async def test_short_content_returns_failed(self):
        agent, _ = make_agent()
        result = await agent.run({"content": "short"})
        assert result["status"] == "failed"

    @pytest.mark.asyncio
    async def test_whitespace_only_content_returns_failed(self):
        agent, _ = make_agent()
        result = await agent.run({"content": "   "})
        assert result["status"] == "failed"

    @pytest.mark.asyncio
    async def test_missing_content_returns_failed(self):
        agent, _ = make_agent()
        result = await agent.run({})
        assert result["status"] == "failed"


# ---------------------------------------------------------------------------
# run() — error handling
# ---------------------------------------------------------------------------


class TestRunErrorHandling:
    @pytest.mark.asyncio
    async def test_db_exception_returns_failed(self):
        agent, db = make_agent()
        db.create_post = AsyncMock(side_effect=RuntimeError("DB write failed"))
        result = await agent.run({"content": LONG_CONTENT})
        assert result["status"] == "failed"
        assert "DB write failed" in result["error"]
        assert result["post_id"] is None
        assert result["slug"] is None
        assert result["url"] is None


# ---------------------------------------------------------------------------
# Lazy initialization
# ---------------------------------------------------------------------------


class TestLazyInit:
    @pytest.mark.asyncio
    async def test_lazy_init_when_no_db_injected(self):
        agent = BlogPublisherAgent(database_service=None)
        assert agent._db_initialized is False

        mock_db = AsyncMock()
        mock_db.create_post = AsyncMock(return_value={"id": "lazy-id"})
        mock_db.initialize = AsyncMock()

        # DatabaseService is imported inside _ensure_database_service, so patch the source module
        with patch("services.database_service.DatabaseService") as MockDB:
            MockDB.return_value = mock_db
            result = await agent.run({"content": LONG_CONTENT})

        assert result["status"] == "success"
        mock_db.initialize.assert_awaited_once()


# ---------------------------------------------------------------------------
# Factory function
# ---------------------------------------------------------------------------


class TestFactory:
    def test_get_blog_publisher_agent_returns_instance(self):
        agent = get_blog_publisher_agent()
        assert isinstance(agent, BlogPublisherAgent)
        assert agent.database_service is None
        assert agent._db_initialized is False
