"""Contract tests for the ``publishing.youtube`` handler shim.

This module adapts the registry's
``(payload, *, site_config, row, pool)`` contract to the
``YouTubePublishAdapter``'s keyword-only ``publish(...)`` signature.
The tests pin:

- The handler is registered under ``("publishing", "youtube")`` so the
  registry's lookup finds it (matches the 2026-05-09 #112 publishing-
  adapter pattern).
- Payload validation: missing required fields raises TypeError BEFORE
  touching Google.
- The adapter call uses the payload's media_path/title/etc. faithfully.
- The shim flattens the adapter's PublishResult dataclass to the
  ``{success, post_id, url, error}`` dict shape every other
  ``publishing.*`` handler returns.
"""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from services.integrations import registry
from services.integrations.handlers import publishing_youtube  # noqa: F401  side-effect: decorator

pytestmark = pytest.mark.asyncio


@pytest.fixture
def stub_site_config() -> Any:
    """Bare SiteConfig stand-in — the shim only passes it through, the
    adapter does the actual gating lookups in its own tests."""
    sc = MagicMock()
    sc.get = MagicMock(return_value=None)
    sc.get_secret = AsyncMock(return_value="")
    return sc


def _publish_result(**overrides) -> Any:
    """Build a fake PublishResult — duck-typed so we don't import the
    real dataclass (keeps tests light + tolerant of adapter refactors).

    All known fields default to concrete values (not MagicMock auto-
    attributes), so a None override actually surfaces as None to the
    handler's ``getattr(..., default)`` fallback logic.
    """
    base = {
        "success": True,
        "platform": "youtube",
        "platform_post_id": "abc123",
        "video_id": None,           # alternate id field the handler also checks
        "url": "https://youtube.com/watch?v=abc123",
        "error": None,
    }
    base.update(overrides)
    obj = MagicMock(spec_set=list(base.keys()))
    for k, v in base.items():
        setattr(obj, k, v)
    return obj


async def test_handler_is_registered() -> None:
    """The decorator runs on import — registry.lookup should succeed."""
    handler = registry.lookup("publishing", "youtube")
    assert handler is not None
    assert callable(handler)


async def test_payload_must_be_dict(stub_site_config) -> None:
    """A non-dict payload is an operator bug — fail loudly before
    touching Google."""
    handler = registry.lookup("publishing", "youtube")
    with pytest.raises(TypeError, match="must be a dict"):
        await handler(
            "not-a-dict", site_config=stub_site_config, row={"name": "youtube_main"},
        )


async def test_payload_must_include_media_path_and_title(stub_site_config) -> None:
    handler = registry.lookup("publishing", "youtube")
    with pytest.raises(TypeError, match="media_path"):
        await handler(
            {"title": "x"},
            site_config=stub_site_config, row={"name": "youtube_main"},
        )
    with pytest.raises(TypeError, match="media_path|title"):
        await handler(
            {"media_path": "/x.mp4"},
            site_config=stub_site_config, row={"name": "youtube_main"},
        )


async def test_handler_dispatches_to_adapter_with_payload_fields(stub_site_config) -> None:
    handler = registry.lookup("publishing", "youtube")
    fake_adapter = MagicMock()
    fake_adapter.publish = AsyncMock(return_value=_publish_result())

    with patch(
        "services.integrations.handlers.publishing_youtube.YouTubePublishAdapter",
        return_value=fake_adapter,
    ):
        result = await handler(
            {
                "media_path": "/tmp/test.mp4",
                "title": "Episode 1",
                "description": "show notes",
                "tags": ["ai", "automation"],
                "post_id": "post-uuid",
            },
            site_config=stub_site_config,
            row={"name": "youtube_main", "platform": "youtube"},
            pool=None,
        )

    fake_adapter.publish.assert_awaited_once()
    call_kwargs = fake_adapter.publish.await_args.kwargs
    assert call_kwargs["media_path"] == "/tmp/test.mp4"
    assert call_kwargs["title"] == "Episode 1"
    assert call_kwargs["description"] == "show notes"
    assert call_kwargs["tags"] == ["ai", "automation"]

    # Result is the flattened dict every other publishing.* handler returns.
    assert result["success"] is True
    assert result["platform"] == "youtube"
    assert result["post_id"] == "abc123"
    assert result["url"] == "https://youtube.com/watch?v=abc123"
    assert result["error"] is None


async def test_handler_propagates_adapter_failure(stub_site_config) -> None:
    """A failure from the adapter (e.g. disabled flag, missing secrets,
    Google API 4xx) flows through to the dict result. The handler MUST
    NOT swallow the error — the caller (social_poster /
    backfill_videos) needs to log + record the failure."""
    handler = registry.lookup("publishing", "youtube")
    fake_adapter = MagicMock()
    fake_adapter.publish = AsyncMock(
        return_value=_publish_result(
            success=False,
            error="youtube adapter disabled in app_settings",
            platform_post_id=None,
        ),
    )

    with patch(
        "services.integrations.handlers.publishing_youtube.YouTubePublishAdapter",
        return_value=fake_adapter,
    ):
        result = await handler(
            {"media_path": "/tmp/test.mp4", "title": "x"},
            site_config=stub_site_config,
            row={"name": "youtube_main"},
        )

    assert result["success"] is False
    assert "disabled" in result["error"]
    assert result["post_id"] is None
