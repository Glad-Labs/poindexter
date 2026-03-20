"""Unit tests for publishing workflow phases."""

import sys
from types import ModuleType
from unittest.mock import AsyncMock, MagicMock

import pytest

# Stub 'slugify' if not installed (optional dependency not in dev environment)
if "slugify" not in sys.modules:
    slugify_stub = ModuleType("slugify")
    setattr(slugify_stub, "slugify", lambda text, **kwargs: text.lower().replace(" ", "-"))
    sys.modules["slugify"] = slugify_stub

from src.cofounder_agent.services.phases.publishing_phases import (
    CreatePostPhase,
    PublishPostPhase,
)


@pytest.mark.unit
@pytest.mark.asyncio
async def test_create_post_phase_persists_post_with_injected_database_service():
    """CreatePostPhase should persist draft content via database service."""
    db_service = AsyncMock()
    db_service.create_post.return_value = {
        "id": "post-123",
        "slug": "ai-future",
        "status": "draft",
    }

    phase = CreatePostPhase("phase-1", "create_post")
    result = await phase.execute(
        {
            "content": "# AI Future",
            "topic": "AI Future",
            "seo_title": "AI Future",
        },
        {
            "database_service": db_service,
            "status": "draft",
        },
    )

    assert result["post_id"] == "post-123"
    assert result["slug"] == "ai-future"
    assert result["status"] == "draft"
    db_service.create_post.assert_awaited_once()


@pytest.mark.unit
@pytest.mark.asyncio
async def test_create_post_phase_accepts_model_like_database_response():
    """CreatePostPhase should parse model-like responses from database service."""

    class FakePostModel:
        def model_dump(self):
            return {
                "id": "post-456",
                "slug": "agent-ops",
                "status": "draft",
            }

    db_service = AsyncMock()
    db_service.create_post.return_value = FakePostModel()

    phase = CreatePostPhase("phase-2", "create_post")
    result = await phase.execute(
        {
            "content": "content",
            "topic": "Agent Ops",
        },
        {"database_service": db_service},
    )

    assert result["post_id"] == "post-456"
    assert result["slug"] == "agent-ops"


@pytest.mark.unit
@pytest.mark.asyncio
async def test_publish_post_phase_updates_post_status_to_published():
    """PublishPostPhase should mark the post as published in the database."""
    db_service = AsyncMock()
    db_service.update_post.return_value = True

    phase = PublishPostPhase("phase-3", "publish_post")
    result = await phase.execute(
        {
            "post_id": "post-123",
            "slug": "ai-future",
        },
        {
            "database_service": db_service,
            "base_url": "https://gladlabs.example",
        },
    )

    assert result["post_id"] == "post-123"
    assert result["public_url"] == "https://gladlabs.example/posts/ai-future"
    db_service.update_post.assert_awaited_once_with("post-123", {"status": "published"})


@pytest.mark.unit
@pytest.mark.asyncio
async def test_publish_post_phase_raises_when_post_not_found():
    """PublishPostPhase should fail when status update returns False."""
    db_service = AsyncMock()
    db_service.update_post.return_value = False

    phase = PublishPostPhase("phase-4", "publish_post")

    with pytest.raises(ValueError, match="Failed to publish post"):
        await phase.execute(
            {
                "post_id": "missing-post",
                "slug": "missing",
            },
            {"database_service": db_service},
        )

    assert phase.status == "failed"
