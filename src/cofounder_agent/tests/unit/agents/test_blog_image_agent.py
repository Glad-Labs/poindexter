"""
Unit tests for agents/blog_image_agent.py — BlogImageAgent
"""

import pytest
from unittest.mock import AsyncMock, MagicMock, patch
from agents.blog_image_agent import BlogImageAgent, get_blog_image_agent


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_image(url="https://example.com/img.jpg"):
    """Create a mock image object with to_dict() and to_markdown()."""
    img = MagicMock()
    img.to_dict.return_value = {"url": url, "alt_text": "test image"}
    img.to_markdown.return_value = f"![test]({url})"
    return img


def make_agent():
    """Build a BlogImageAgent with a mocked image service."""
    with patch("agents.blog_image_agent.get_image_service") as mock_factory:
        mock_svc = AsyncMock()
        mock_factory.return_value = mock_svc
        agent = BlogImageAgent()
        agent.image_service = mock_svc
    return agent, mock_svc


# ---------------------------------------------------------------------------
# run() — success paths
# ---------------------------------------------------------------------------


class TestRunSuccess:
    @pytest.mark.asyncio
    async def test_returns_success_with_featured_image(self):
        agent, svc = make_agent()
        img = _make_image()
        svc.search_featured_image = AsyncMock(return_value=img)
        svc.get_images_for_gallery = AsyncMock(return_value=[])

        result = await agent.run({"topic": "ocean sunset"})

        assert result["status"] == "success"
        assert result["featured_image"] == {"url": "https://example.com/img.jpg", "alt_text": "test image"}
        assert "ocean sunset" in result["image_markdown"] or "example.com" in result["image_markdown"]
        assert result["gallery_images"] == []

    @pytest.mark.asyncio
    async def test_no_featured_image_returns_success_with_none(self):
        agent, svc = make_agent()
        svc.search_featured_image = AsyncMock(return_value=None)
        svc.get_images_for_gallery = AsyncMock(return_value=[])

        result = await agent.run({"topic": "abstract concept"})

        assert result["status"] == "success"
        assert result["featured_image"] is None
        assert result["image_markdown"] == ""

    @pytest.mark.asyncio
    async def test_gallery_images_fetched_when_image_count_gt_1(self):
        agent, svc = make_agent()
        img = _make_image()
        gallery = [_make_image("https://g1.com"), _make_image("https://g2.com")]
        svc.search_featured_image = AsyncMock(return_value=img)
        svc.get_images_for_gallery = AsyncMock(return_value=gallery)

        result = await agent.run({"topic": "nature photos", "image_count": 3})

        svc.get_images_for_gallery.assert_awaited_once_with(
            topic="nature photos",
            count=3,
            keywords=[],
        )
        assert len(result["gallery_images"]) == 2
        assert result["image_count_found"] == 2

    @pytest.mark.asyncio
    async def test_gallery_not_fetched_when_image_count_is_1(self):
        agent, svc = make_agent()
        img = _make_image()
        svc.search_featured_image = AsyncMock(return_value=img)
        svc.get_images_for_gallery = AsyncMock(return_value=[])

        await agent.run({"topic": "single image topic", "image_count": 1})

        svc.get_images_for_gallery.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_passes_correct_params_to_search(self):
        agent, svc = make_agent()
        img = _make_image()
        svc.search_featured_image = AsyncMock(return_value=img)
        svc.get_images_for_gallery = AsyncMock(return_value=[])

        await agent.run(
            {
                "topic": "mountains",
                "keywords": ["alps", "snow"],
                "orientation": "portrait",
                "size": "large",
                "page": 2,
            }
        )

        svc.search_featured_image.assert_awaited_once_with(
            topic="mountains",
            keywords=["alps", "snow"],
            orientation="portrait",
            size="large",
            page=2,
        )

    @pytest.mark.asyncio
    async def test_defaults_applied_when_params_omitted(self):
        agent, svc = make_agent()
        img = _make_image()
        svc.search_featured_image = AsyncMock(return_value=img)
        svc.get_images_for_gallery = AsyncMock(return_value=[])

        await agent.run({"topic": "default test topic"})

        call_kwargs = svc.search_featured_image.call_args.kwargs
        assert call_kwargs["keywords"] == []
        assert call_kwargs["orientation"] == "landscape"
        assert call_kwargs["size"] == "medium"
        assert call_kwargs["page"] == 1


# ---------------------------------------------------------------------------
# run() — validation errors
# ---------------------------------------------------------------------------


class TestRunValidation:
    @pytest.mark.asyncio
    async def test_empty_topic_returns_failed(self):
        agent, _ = make_agent()
        result = await agent.run({"topic": ""})
        assert result["status"] == "failed"
        assert result["featured_image"] is None

    @pytest.mark.asyncio
    async def test_short_topic_returns_failed(self):
        agent, _ = make_agent()
        result = await agent.run({"topic": "xy"})
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
    async def test_service_exception_returns_failed(self):
        agent, svc = make_agent()
        svc.search_featured_image = AsyncMock(side_effect=RuntimeError("Pexels API down"))
        result = await agent.run({"topic": "error case topic"})
        assert result["status"] == "failed"
        assert "Pexels API down" in result["error"]
        assert result["featured_image"] is None
        assert result["gallery_images"] == []
        assert result["image_markdown"] == ""


# ---------------------------------------------------------------------------
# Factory function
# ---------------------------------------------------------------------------


class TestFactory:
    def test_get_blog_image_agent_returns_instance(self):
        with patch("agents.blog_image_agent.get_image_service"):
            agent = get_blog_image_agent()
        assert isinstance(agent, BlogImageAgent)
