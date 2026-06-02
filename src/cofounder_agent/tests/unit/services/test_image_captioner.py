"""Unit tests for services.image_captioner — vision-based alt text."""
import base64

import pytest
from unittest.mock import AsyncMock, patch

from services.image_captioner import caption_image


class _Result:
    def __init__(self, text):
        self.text = text


@pytest.mark.asyncio
async def test_caption_image_happy_path_strips_image_of_prefix():
    png = base64.b64encode(b"\x89PNG\r\n").decode()
    with patch("services.image_captioner._fetch_b64", AsyncMock(return_value=png)), patch(
        "services.image_captioner.dispatch_complete",
        AsyncMock(return_value=_Result("Image of a teal glass cube on blueprint paper.")),
    ) as disp:
        alt = await caption_image(
            image_url="https://r2/x.png",
            topic="CAD",
            budget=120,
            site_config=None,
            pool=object(),
        )
    # dispatch_complete called with an OpenAI-style image content block
    msgs = disp.call_args.kwargs["messages"]
    assert isinstance(msgs[0]["content"], list)
    assert any(p.get("type") == "image_url" for p in msgs[0]["content"])
    # Regression guard: the GENERATION token budget must be generous —
    # NOT the ~120 char alt budget. qwen3-vl reasons before answering; a
    # small cap returns empty content. (Verified empirically 2026-06-02.)
    assert disp.call_args.kwargs["max_tokens"] >= 1024
    # sanitized: no "Image of" prefix, within char budget
    assert alt is not None
    assert not alt.lower().startswith("image of")
    assert len(alt) <= 120


@pytest.mark.asyncio
async def test_caption_image_fail_soft_returns_none_on_fetch_error():
    with patch("services.image_captioner._fetch_b64", AsyncMock(return_value=None)):
        alt = await caption_image(
            image_url="https://r2/x.png",
            topic="CAD",
            budget=120,
            site_config=None,
            pool=object(),
        )
    assert alt is None


@pytest.mark.asyncio
async def test_caption_image_fail_soft_on_dispatch_error():
    png = base64.b64encode(b"x").decode()
    with patch("services.image_captioner._fetch_b64", AsyncMock(return_value=png)), patch(
        "services.image_captioner.dispatch_complete",
        AsyncMock(side_effect=RuntimeError("ollama down")),
    ):
        alt = await caption_image(
            image_url="https://r2/x.png",
            topic="CAD",
            budget=120,
            site_config=None,
            pool=object(),
        )
    assert alt is None
