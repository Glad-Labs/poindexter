"""Unit tests for ``services/image_providers/sdxl.py``.

The underlying ImageService (torch/diffusers/GPU) is mocked so the
tests run without a GPU. Focus: Protocol conformance, empty-prompt
handling, failure paths, and the upload_to knob.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from services.image_providers.sdxl import SdxlProvider


@pytest.mark.unit
class TestSdxlProviderMetadata:
    def test_name(self):
        assert SdxlProvider.name == "sdxl"

    def test_kind_is_generate(self):
        assert SdxlProvider.kind == "generate"


@pytest.mark.unit
@pytest.mark.asyncio
class TestSdxlProviderFetch:
    async def test_empty_prompt_returns_empty(self):
        result = await SdxlProvider().fetch("", {})
        assert result == []

    async def test_whitespace_prompt_returns_empty(self):
        result = await SdxlProvider().fetch("   ", {})
        assert result == []

    async def test_generate_success_returns_file_url(self, tmp_path):
        svc = MagicMock()
        svc.generate_image = AsyncMock(return_value=True)

        fake_tmp = MagicMock()
        fake_tmp.name = str(tmp_path / "out.png")

        fake_ctx = MagicMock()
        fake_ctx.__enter__ = MagicMock(return_value=fake_tmp)
        fake_ctx.__exit__ = MagicMock(return_value=False)

        # The generator writes the file so the post-call existence check passes.
        (tmp_path / "out.png").write_bytes(b"fake png")

        fake_image_service = MagicMock()
        fake_image_service.get_image_service = MagicMock(return_value=svc)

        with patch.dict(
            "sys.modules",
            {"services.image_service": fake_image_service},
        ), \
             patch("tempfile.NamedTemporaryFile", return_value=fake_ctx):
            results = await SdxlProvider().fetch("a cinematic scene", {})

        assert len(results) == 1
        assert results[0].url.startswith("file://")
        assert results[0].source == "sdxl"
        assert results[0].search_query == "a cinematic scene"
        svc.generate_image.assert_awaited_once()

    async def test_generate_failure_returns_empty(self):
        svc = MagicMock()
        svc.generate_image = AsyncMock(return_value=False)

        fake_tmp = MagicMock()
        fake_tmp.name = "/tmp/fake.png"

        fake_ctx = MagicMock()
        fake_ctx.__enter__ = MagicMock(return_value=fake_tmp)
        fake_ctx.__exit__ = MagicMock(return_value=False)

        fake_image_service = MagicMock()
        fake_image_service.get_image_service = MagicMock(return_value=svc)

        with patch.dict(
            "sys.modules",
            {"services.image_service": fake_image_service},
        ), \
             patch("tempfile.NamedTemporaryFile", return_value=fake_ctx), \
             patch("os.path.exists", return_value=False):
            results = await SdxlProvider().fetch("a scene", {})

        assert results == []

    async def test_generate_exception_returns_empty(self, tmp_path):
        svc = MagicMock()
        svc.generate_image = AsyncMock(side_effect=RuntimeError("GPU OOM"))

        fake_tmp = MagicMock()
        fake_tmp.name = str(tmp_path / "out.png")

        fake_ctx = MagicMock()
        fake_ctx.__enter__ = MagicMock(return_value=fake_tmp)
        fake_ctx.__exit__ = MagicMock(return_value=False)

        fake_image_service = MagicMock()
        fake_image_service.get_image_service = MagicMock(return_value=svc)

        with patch.dict(
            "sys.modules",
            {"services.image_service": fake_image_service},
        ), \
             patch("tempfile.NamedTemporaryFile", return_value=fake_ctx):
            results = await SdxlProvider().fetch("a scene", {})

        assert results == []

    async def test_negative_prompt_from_config_wins(self, tmp_path):
        svc = MagicMock()
        svc.generate_image = AsyncMock(return_value=True)

        fake_tmp = MagicMock()
        fake_tmp.name = str(tmp_path / "out.png")
        (tmp_path / "out.png").write_bytes(b"fake png")

        fake_ctx = MagicMock()
        fake_ctx.__enter__ = MagicMock(return_value=fake_tmp)
        fake_ctx.__exit__ = MagicMock(return_value=False)

        fake_image_service = MagicMock()
        fake_image_service.get_image_service = MagicMock(return_value=svc)

        with patch.dict(
            "sys.modules",
            {"services.image_service": fake_image_service},
        ), \
             patch("tempfile.NamedTemporaryFile", return_value=fake_ctx):
            await SdxlProvider().fetch("x", {"negative_prompt": "no watermark"})

        # The negative_prompt from config was forwarded to generate_image.
        call = svc.generate_image.await_args
        assert call.kwargs["negative_prompt"] == "no watermark"

    async def test_upload_to_cloudinary_triggers_upload(self, tmp_path):
        svc = MagicMock()
        svc.generate_image = AsyncMock(return_value=True)

        fake_tmp = MagicMock()
        fake_tmp.name = str(tmp_path / "out.png")
        (tmp_path / "out.png").write_bytes(b"fake png")

        fake_ctx = MagicMock()
        fake_ctx.__enter__ = MagicMock(return_value=fake_tmp)
        fake_ctx.__exit__ = MagicMock(return_value=False)

        fake_image_service = MagicMock()
        fake_image_service.get_image_service = MagicMock(return_value=svc)

        with patch.dict(
            "sys.modules",
            {"services.image_service": fake_image_service},
        ), \
             patch("tempfile.NamedTemporaryFile", return_value=fake_ctx), \
             patch(
                "services.image_providers.sdxl._upload_to_cloudinary",
                new=AsyncMock(return_value="https://cdn.cloudinary/x.png"),
             ) as up:
            results = await SdxlProvider().fetch("x", {"upload_to": "cloudinary"})

        assert results[0].url == "https://cdn.cloudinary/x.png"
        up.assert_awaited_once()

    async def test_image_service_missing_returns_empty(self, tmp_path):
        """If services.image_service can't be imported (torch missing etc.),
        the provider must return [] instead of raising."""
        with patch.dict("sys.modules", {"services.image_service": None}):
            results = await SdxlProvider().fetch("x", {})
        assert results == []
