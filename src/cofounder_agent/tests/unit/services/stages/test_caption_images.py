"""Unit tests for CaptionImagesStage — vision alt for inline + featured."""
import pytest
from unittest.mock import AsyncMock, patch

from modules.content.stages.caption_images import CaptionImagesStage


@pytest.mark.asyncio
async def test_caption_images_rewrites_inline_alt_and_featured():
    content = (
        '<img src="https://r2/a.png" alt="A photo of a person at a desk" '
        'width="1024" height="1024" loading="lazy" />'
    )
    ctx = {
        "content": content,
        "topic": "Async APIs",
        "featured_image_url": "https://r2/feat.png",
        "featured_image_alt": "Async APIs — AI generated illustration",
        "site_config": None,
        "database_service": None,
    }

    async def fake_caption(*, image_url, **kw):
        return (
            "Monitor showing circuit schematic, backlit keyboard"
            if "a.png" in image_url
            else "Abstract cyan and navy geometric cover"
        )

    with patch(
        "modules.content.stages.caption_images.caption_image",
        AsyncMock(side_effect=fake_caption),
    ):
        res = await CaptionImagesStage().execute(ctx, {})

    assert res.ok
    new_content = res.context_updates["content"]
    assert 'alt="Monitor showing circuit schematic, backlit keyboard"' in new_content
    assert "a photo of a person" not in new_content.lower()
    assert (
        res.context_updates["featured_image_alt"]
        == "Abstract cyan and navy geometric cover"
    )


@pytest.mark.asyncio
async def test_caption_images_failsoft_keeps_prior_alt():
    content = '<img src="https://r2/a.png" alt="original alt" />'
    ctx = {"content": content, "topic": "X", "site_config": None}
    with patch(
        "modules.content.stages.caption_images.caption_image",
        AsyncMock(return_value=None),
    ):
        res = await CaptionImagesStage().execute(ctx, {})
    assert 'alt="original alt"' in res.context_updates["content"]


@pytest.mark.asyncio
async def test_caption_images_strips_double_quotes_from_caption():
    content = '<img src="https://r2/a.png" alt="old" />'
    ctx = {"content": content, "topic": "X", "site_config": None}
    with patch(
        "modules.content.stages.caption_images.caption_image",
        AsyncMock(return_value='A sign reading "OPEN" on a door'),
    ):
        res = await CaptionImagesStage().execute(ctx, {})
    # No raw double-quote inside the alt value (would break the attribute)
    new_content = res.context_updates["content"]
    assert 'alt="A sign reading' in new_content
    assert '""' not in new_content
