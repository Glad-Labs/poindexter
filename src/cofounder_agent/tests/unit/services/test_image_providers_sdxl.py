"""Unit tests for ``services/image_providers/sdxl.py``.

Post-Phase-G (GH#71) the SdxlProvider owns the full generation
lifecycle — host SDXL sidecar HTTP path + in-process diffusers
fallback + upload targets. These tests mock ``httpx.AsyncClient`` to
avoid touching the real sidecar and mock the in-process pipeline state
to avoid loading a 6GB model.
"""

from __future__ import annotations

from contextlib import contextmanager
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from plugins.image_provider import ImageProvider, ImageResult
from services.image_providers import sdxl as sdxl_mod
from services.image_providers.sdxl import SdxlProvider


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _image_response(content: bytes = b"\x89PNG fake", elapsed: str = "1.2"):
    """Fake httpx Response representing a successful SDXL sidecar reply."""
    resp = MagicMock()
    resp.status_code = 200
    resp.headers = {"content-type": "image/png", "X-Elapsed-Seconds": elapsed}
    resp.content = content
    return resp


def _error_response(status: int = 500, text: str = "internal error"):
    resp = MagicMock()
    resp.status_code = status
    resp.headers = {"content-type": "text/plain"}
    resp.text = text
    resp.content = b""
    return resp


def _html_response():
    resp = MagicMock()
    resp.status_code = 200
    resp.headers = {"content-type": "text/html"}
    resp.text = "<html>error</html>"
    resp.content = b""
    return resp


@contextmanager
def _mock_httpx_post(response):
    """Patch httpx.AsyncClient to yield a client whose .post returns ``response``.

    Used to simulate the host SDXL sidecar — when ``response`` raises,
    the provider's sidecar path falls through to diffusers.
    """
    client = AsyncMock()
    client.__aenter__ = AsyncMock(return_value=client)
    client.__aexit__ = AsyncMock(return_value=False)
    if isinstance(response, Exception):
        client.post = AsyncMock(side_effect=response)
    else:
        client.post = AsyncMock(return_value=response)
    with patch("services.image_providers.sdxl.httpx.AsyncClient", return_value=client):
        yield client


@contextmanager
def _diffusers_unavailable():
    """Disable the in-process diffusers fallback for a test.

    Patches ``_state`` so ``_try_in_process_diffusers`` short-circuits to
    False without touching torch/diffusers. Restores state after.
    """
    original_state = sdxl_mod._state
    fresh = sdxl_mod._SdxlPipelineState()
    fresh.initialized = True
    fresh.available = False
    sdxl_mod._state = fresh
    try:
        yield
    finally:
        sdxl_mod._state = original_state


# ---------------------------------------------------------------------------
# Metadata / Protocol conformance
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestSdxlProviderMetadata:
    def test_name(self):
        assert SdxlProvider.name == "sdxl"

    def test_kind_is_generate(self):
        assert SdxlProvider.kind == "generate"


@pytest.mark.unit
class TestContract:
    def test_conforms_to_image_provider_protocol(self):
        provider = SdxlProvider()
        assert isinstance(provider, ImageProvider)

    def test_fetch_is_coroutine(self):
        import inspect
        assert inspect.iscoroutinefunction(SdxlProvider.fetch)


# ---------------------------------------------------------------------------
# SdxlProvider.fetch — sidecar happy path + fallback matrix
# ---------------------------------------------------------------------------


@pytest.mark.unit
@pytest.mark.asyncio
class TestSdxlProviderFetch:
    async def test_empty_prompt_returns_empty(self):
        assert await SdxlProvider().fetch("", {}) == []

    async def test_whitespace_prompt_returns_empty(self):
        assert await SdxlProvider().fetch("   ", {}) == []

    async def test_host_sidecar_success_returns_file_url(self, tmp_path):
        """Sidecar returns image bytes → bytes written to output_path,
        ImageResult carries file:// URL."""
        output_path = str(tmp_path / "out.png")
        resp = _image_response(content=b"\x89PNG_generated")
        with _mock_httpx_post(resp):
            results = await SdxlProvider().fetch(
                "a cinematic scene",
                {"output_path": output_path},
            )

        assert len(results) == 1
        r = results[0]
        assert isinstance(r, ImageResult)
        assert r.url == f"file://{output_path}"
        assert r.source == "sdxl"
        assert r.search_query == "a cinematic scene"
        assert r.metadata["local_path"] == output_path
        # File was actually written
        assert (tmp_path / "out.png").read_bytes() == b"\x89PNG_generated"

    async def test_sidecar_500_with_diffusers_unavailable_returns_empty(
        self, tmp_path,
    ):
        with _mock_httpx_post(_error_response(500)), _diffusers_unavailable():
            results = await SdxlProvider().fetch(
                "x", {"output_path": str(tmp_path / "x.png")},
            )
        assert results == []

    async def test_sidecar_exception_with_diffusers_unavailable_returns_empty(
        self, tmp_path,
    ):
        with (
            _mock_httpx_post(RuntimeError("connection refused")),
            _diffusers_unavailable(),
        ):
            results = await SdxlProvider().fetch(
                "x", {"output_path": str(tmp_path / "x.png")},
            )
        assert results == []

    async def test_sidecar_wrong_content_type_with_diffusers_unavailable_returns_empty(
        self, tmp_path,
    ):
        """200 with text/html is a sidecar error page — fall through."""
        with _mock_httpx_post(_html_response()), _diffusers_unavailable():
            results = await SdxlProvider().fetch(
                "x", {"output_path": str(tmp_path / "x.png")},
            )
        assert results == []

    async def test_tempfile_used_when_output_path_not_set(self, tmp_path):
        """No output_path in config → provider uses a tempfile."""
        resp = _image_response(content=b"\x89PNG_tempfile")

        captured_path: dict[str, str] = {}

        orig_open = open

        def spy_open(path, *args, **kwargs):  # noqa: ANN001 — passthrough
            captured_path["path"] = str(path)
            return orig_open(path, *args, **kwargs)

        with _mock_httpx_post(resp), patch(
            "services.image_providers.sdxl.open", spy_open, create=True,
        ):
            results = await SdxlProvider().fetch("x", {})

        assert len(results) == 1
        # URL should reference a tempfile path the provider created
        assert results[0].url.startswith("file://")
        assert "local_path" in results[0].metadata

    async def test_negative_prompt_from_config_wins(self, tmp_path):
        captured: dict = {}

        async def capture_post(url, json=None, timeout=None):
            captured["json"] = json
            return _image_response(content=b"\x89PNG")

        client = AsyncMock()
        client.__aenter__ = AsyncMock(return_value=client)
        client.__aexit__ = AsyncMock(return_value=False)
        client.post = AsyncMock(side_effect=capture_post)

        with patch(
            "services.image_providers.sdxl.httpx.AsyncClient", return_value=client,
        ):
            await SdxlProvider().fetch(
                "a scene",
                {
                    "output_path": str(tmp_path / "o.png"),
                    "negative_prompt": "no watermark",
                },
            )

        assert captured["json"]["negative_prompt"] == "no watermark"

    async def test_steps_and_guidance_forwarded_to_sidecar(self, tmp_path):
        captured: dict = {}

        async def capture_post(url, json=None, timeout=None):
            captured["json"] = json
            return _image_response(content=b"\x89PNG")

        client = AsyncMock()
        client.__aenter__ = AsyncMock(return_value=client)
        client.__aexit__ = AsyncMock(return_value=False)
        client.post = AsyncMock(side_effect=capture_post)

        with patch(
            "services.image_providers.sdxl.httpx.AsyncClient", return_value=client,
        ):
            await SdxlProvider().fetch(
                "a scene",
                {
                    "output_path": str(tmp_path / "o.png"),
                    "num_inference_steps": 12,
                    "guidance_scale": 3.5,
                },
            )

        assert captured["json"]["steps"] == 12
        assert captured["json"]["guidance_scale"] == 3.5

    async def test_upload_to_cloudinary_triggers_upload(self, tmp_path):
        resp = _image_response(content=b"\x89PNG")
        with _mock_httpx_post(resp), patch(
            "services.image_providers.sdxl._upload_to_cloudinary",
            new=AsyncMock(return_value="https://cdn.cloudinary/x.png"),
        ) as up:
            results = await SdxlProvider().fetch(
                "x",
                {
                    "output_path": str(tmp_path / "o.png"),
                    "upload_to": "cloudinary",
                },
            )

        assert results[0].url == "https://cdn.cloudinary/x.png"
        up.assert_awaited_once()

    async def test_upload_to_r2_triggers_upload(self, tmp_path):
        resp = _image_response(content=b"\x89PNG")
        with _mock_httpx_post(resp), patch(
            "services.image_providers.sdxl._upload_to_r2",
            new=AsyncMock(return_value="https://cdn.r2/x.png"),
        ) as up:
            results = await SdxlProvider().fetch(
                "x",
                {
                    "output_path": str(tmp_path / "o.png"),
                    "upload_to": "r2",
                },
            )

        assert results[0].url == "https://cdn.r2/x.png"
        up.assert_awaited_once()

    async def test_cloudinary_upload_failure_falls_back_to_file_url(
        self, tmp_path,
    ):
        """When the upload raises, the provider keeps the local file:// URL."""
        output_path = str(tmp_path / "o.png")
        resp = _image_response(content=b"\x89PNG")
        with _mock_httpx_post(resp), patch(
            "services.image_providers.sdxl._upload_to_cloudinary",
            new=AsyncMock(side_effect=RuntimeError("auth failed")),
        ):
            results = await SdxlProvider().fetch(
                "x",
                {"output_path": output_path, "upload_to": "cloudinary"},
            )

        assert len(results) == 1
        assert results[0].url == f"file://{output_path}"
