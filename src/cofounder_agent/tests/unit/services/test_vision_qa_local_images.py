"""Vision QA reads ``storage_provider=local`` images from the folder.

A local image's URL is ``{api_url}/site/…``, which the browser can reach but
the flow container can't. Before this, every local image failed to download,
and the rail returned "no images could be fetched" on every post: vision QA was
off in local mode without anyone deciding so.
"""

from __future__ import annotations

import base64
import io
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from poindexter.modules.content.multi_model_qa import MultiModelQA
from poindexter.services.site_config import SiteConfig


class _Settings:
    def __init__(self, values):
        self.values = values

    async def get(self, key, default=None):
        return self.values.get(key, default)


def _webp_bytes() -> bytes:
    from PIL import Image

    buf = io.BytesIO()
    Image.new("RGB", (24, 24), (30, 110, 220)).save(buf, format="WEBP")
    return buf.getvalue()


@pytest.mark.asyncio
async def test_a_local_image_reaches_the_judge_without_an_http_fetch(tmp_path):
    root = tmp_path / "site"
    image = root / "images" / "inline" / "a.webp"
    image.parent.mkdir(parents=True)
    image.write_bytes(_webp_bytes())
    qa = MultiModelQA(
        pool=None,
        settings_service=_Settings(
            {"qa_vision_check_enabled": "true", "qa_vision_model": "ollama/vision-judge"}
        ),
        site_config=SiteConfig(
            initial_config={
                "storage_provider": "local",
                "storage_local_dir": str(root),
                "api_url": "http://localhost:8002",
            },
        ),
    )
    judge = AsyncMock(return_value=None)
    client = MagicMock()
    client.return_value.__aenter__ = AsyncMock(
        return_value=MagicMock(get=AsyncMock(side_effect=AssertionError("HTTP fetch")))
    )
    client.return_value.__aexit__ = AsyncMock(return_value=False)
    content = "Intro.\n\n![A diagram](http://localhost:8002/site/images/inline/a.webp)\n\nBody."
    with patch.object(qa, "_vision_complete", judge), patch("httpx.AsyncClient", client):
        await qa._check_image_relevance("Title", "topic", content)

    judge.assert_awaited_once()
    [sent] = judge.await_args.kwargs["images_b64"]
    # Normalised to JPEG for the vision model, so it decodes as an image.
    assert base64.b64decode(sent)[:2] == b"\xff\xd8"
