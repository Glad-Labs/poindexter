"""Unit tests for services.image_captioner — vision-based alt text."""
import base64
from unittest.mock import AsyncMock, patch

import pytest

from poindexter.services.image_captioner import caption_image


class _Result:
    def __init__(self, text):
        self.text = text


@pytest.mark.asyncio
async def test_caption_image_happy_path_strips_image_of_prefix():
    png = base64.b64encode(b"\x89PNG\r\n").decode()
    with patch("poindexter.services.image_captioner._fetch_b64", AsyncMock(return_value=png)), patch(
        "poindexter.services.image_captioner.dispatch_complete",
        AsyncMock(return_value=_Result("Image of a teal glass cube on blueprint paper.")),
    ) as disp:
        alt = await caption_image(
            image_url="https://r2/x.png",
            topic="CAD",
            budget=120,
            site_config=None,
            pool=object(),
            # poindexter#716 — explicit model bypasses the settings lookup so
            # the test doesn't need a DB pool for resolve_tier_model.
            model="qwen3-vl:30b",
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
    with patch("poindexter.services.image_captioner._fetch_b64", AsyncMock(return_value=None)):
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
    with patch("poindexter.services.image_captioner._fetch_b64", AsyncMock(return_value=png)), patch(
        "poindexter.services.image_captioner.dispatch_complete",
        AsyncMock(side_effect=RuntimeError("ollama down")),
    ):
        alt = await caption_image(
            image_url="https://r2/x.png",
            topic="CAD",
            budget=120,
            site_config=None,
            pool=object(),
            model="qwen3-vl:30b",
        )
    assert alt is None


@pytest.mark.asyncio
async def test_caption_image_skips_when_no_model_configured():
    """poindexter#716: no model configured (vision_alt_model unset) → None, no dispatch."""
    png = base64.b64encode(b"\x89PNG\r\n").decode()
    with patch("poindexter.services.image_captioner._fetch_b64", AsyncMock(return_value=png)), patch(
        "poindexter.services.image_captioner.dispatch_complete",
        AsyncMock(return_value=_Result("some alt")),
    ) as disp:
        alt = await caption_image(
            image_url="https://r2/x.png",
            topic="CAD",
            budget=120,
            site_config=None,  # no site_config → falls back to _DEFAULT_VISION_MODEL=""
            pool=object(),
            model=None,  # no explicit model
        )
    assert alt is None
    disp.assert_not_awaited()


# ---------------------------------------------------------------------------
# storage_provider=local: the image is read from the folder, not over HTTP.
# Its URL is the browser-facing {api_url}/site/..., which doesn't reach the
# worker from inside the Prefect container.
# ---------------------------------------------------------------------------


def _local_site_config(root):
    from poindexter.services.site_config import SiteConfig

    return SiteConfig(
        initial_config={
            "storage_provider": "local",
            "storage_local_dir": str(root),
            "api_url": "http://localhost:8002",
        },
    )


@pytest.mark.asyncio
async def test_fetch_b64_reads_a_local_image_from_disk(tmp_path):
    from poindexter.services.image_captioner import _fetch_b64

    image = tmp_path / "site" / "images" / "inline" / "a.webp"
    image.parent.mkdir(parents=True)
    image.write_bytes(b"RIFF0000WEBPVP8 ")
    boom = AsyncMock(side_effect=AssertionError("must not fetch over HTTP"))
    with patch("poindexter.services.image_captioner.httpx.AsyncClient", boom):
        got = await _fetch_b64(
            "http://localhost:8002/site/images/inline/a.webp",
            site_config=_local_site_config(tmp_path / "site"),
        )
    assert got == base64.b64encode(b"RIFF0000WEBPVP8 ").decode()
    boom.assert_not_called()


@pytest.mark.asyncio
async def test_fetch_b64_still_uses_http_for_other_urls(tmp_path):
    from poindexter.services.image_captioner import _fetch_b64

    class _Resp:
        status_code = 200
        content = b"jpegbytes"

    class _Client:
        def __init__(self, *a, **k):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *exc):
            return False

        async def get(self, url):
            assert url == "https://images.pexels.com/p.jpeg"
            return _Resp()

    with patch("poindexter.services.image_captioner.httpx.AsyncClient", _Client):
        got = await _fetch_b64(
            "https://images.pexels.com/p.jpeg",
            site_config=_local_site_config(tmp_path / "site"),
        )
    assert got == base64.b64encode(b"jpegbytes").decode()


@pytest.mark.asyncio
async def test_caption_image_passes_its_site_config_to_the_fetch():
    fetch = AsyncMock(return_value=None)
    cfg = object()
    with patch("poindexter.services.image_captioner._fetch_b64", fetch):
        await caption_image(
            image_url="http://x/a.webp", topic="t", budget=50, site_config=cfg, pool=None,
        )
    assert fetch.await_args.kwargs["site_config"] is cfg
