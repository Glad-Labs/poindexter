"""
Unit tests for services/image_service.py

Tests FeaturedImageMetadata (to_dict, to_markdown), ImageService initialization,
search_featured_image, get_images_for_gallery, _pexels_search (mocked httpx),
the image-gen HTTP render path and the outcome each failure maps to (mocked
httpx), generate_image_markdown, cache helpers, and factory. The GPU-lock
wrapper around the render is covered in test_image_service_vram_guard.py.
"""

import inspect
import logging
from contextlib import asynccontextmanager
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from poindexter.services import gpu_scheduler as gpu_scheduler_mod
from poindexter.services import image_service as image_service_mod
from poindexter.services.gpu_admission import GpuBusyError
from poindexter.services.image_service import (
    FeaturedImageMetadata,
    ImageGenOutcome,
    ImageModel,
    ImageService,
    get_image_service,
)
from poindexter.services.site_config import SiteConfig

# SiteConfig DI (#272 Phase-2e): the module-level ``site_config`` global +
# ``set_site_config`` were removed; ``ImageService`` / ``get_image_service``
# take a required ``site_config=``. Tests build a fresh env-backed instance
# (mirrors the old module-default behaviour, which read env via
# ``SiteConfig().get``).


def _test_sc() -> SiteConfig:
    """Fresh env-backed SiteConfig for the required ``site_config=`` kwarg."""
    return SiteConfig()

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

SAMPLE_PHOTO = {
    "src": {
        "large": "https://pexels.com/photo/large.jpg",
        "small": "https://pexels.com/photo/small.jpg",
    },
    "photographer": "Jane Doe",
    "photographer_url": "https://pexels.com/@jane",
    "width": 1920,
    "height": 1080,
    "alt": "A beautiful landscape",
}


def make_mock_httpx_response(data: dict, status_code: int = 200) -> MagicMock:
    resp = MagicMock()
    resp.status_code = status_code
    resp.json.return_value = data
    resp.raise_for_status = MagicMock()
    return resp


@asynccontextmanager
async def mock_async_client(response):
    client = AsyncMock()
    client.get = AsyncMock(return_value=response)
    yield client


def make_image_service_with_key() -> ImageService:
    """Return an ImageService with a fake Pexels API key injected.

    Post-encrypt refactor: ``ImageService()`` no longer reads the key
    at __init__ (secrets aren't in site_config; require an async DB
    fetch). Tests set the fields directly here and flip
    ``_pexels_key_checked_db`` so the lazy DB lookup is skipped.
    """
    svc = ImageService(site_config=_test_sc())
    svc.pexels_api_key = "fake-pexels-key"
    svc.pexels_available = True
    svc.pexels_headers = {"Authorization": "fake-pexels-key"}
    svc._pexels_key_checked_db = True
    return svc


def make_image_service_no_key() -> ImageService:
    """Return an ImageService without Pexels API key."""
    with patch.dict("os.environ", {}, clear=True):
        import os

        os.environ.pop("PEXELS_API_KEY", None)
        return ImageService(site_config=_test_sc())


# ---------------------------------------------------------------------------
# FeaturedImageMetadata
# ---------------------------------------------------------------------------


class TestFeaturedImageMetadata:
    def _make_meta(self, **kwargs) -> FeaturedImageMetadata:
        defaults = {
            "url": "https://example.com/photo.jpg",
            "thumbnail": "https://example.com/thumb.jpg",
            "photographer": "John Smith",
            "photographer_url": "https://example.com/@john",
            "width": 1920,
            "height": 1080,
            "alt_text": "A photo",
            "caption": "Photo caption",
            "source": "pexels",
            "search_query": "nature",
        }
        defaults.update(kwargs)
        return FeaturedImageMetadata(**defaults)  # type: ignore[arg-type]

    def test_to_dict_contains_url(self):
        meta = self._make_meta()
        d = meta.to_dict()
        assert d["url"] == "https://example.com/photo.jpg"

    def test_to_dict_contains_photographer(self):
        meta = self._make_meta()
        d = meta.to_dict()
        assert d["photographer"] == "John Smith"

    def test_to_dict_contains_source(self):
        meta = self._make_meta()
        assert meta.to_dict()["source"] == "pexels"

    def test_to_dict_contains_retrieved_at(self):
        meta = self._make_meta()
        d = meta.to_dict()
        assert "retrieved_at" in d

    def test_thumbnail_falls_back_to_url(self):
        meta = FeaturedImageMetadata(url="https://example.com/photo.jpg")
        assert meta.thumbnail == "https://example.com/photo.jpg"

    def test_to_markdown_contains_url(self):
        meta = self._make_meta()
        md = meta.to_markdown()
        assert "https://example.com/photo.jpg" in md

    def test_to_markdown_includes_photographer(self):
        meta = self._make_meta()
        md = meta.to_markdown()
        assert "John Smith" in md

    def test_to_markdown_includes_photographer_link_when_url_set(self):
        meta = self._make_meta()
        md = meta.to_markdown()
        assert "[John Smith](https://example.com/@john)" in md

    def test_to_markdown_caption_override(self):
        meta = self._make_meta()
        md = meta.to_markdown(caption_override="My Custom Caption")
        assert "My Custom Caption" in md

    def test_to_markdown_falls_back_to_alt_text(self):
        meta = self._make_meta(caption="", alt_text="alt text description")
        md = meta.to_markdown()
        assert "alt text description" in md


# ---------------------------------------------------------------------------
# ImageService.__init__
# ---------------------------------------------------------------------------


class TestImageServiceInit:
    def test_pexels_available_with_key(self):
        svc = make_image_service_with_key()
        assert svc.pexels_available is True

    def test_pexels_not_available_without_key(self, monkeypatch):
        monkeypatch.delenv("PEXELS_API_KEY", raising=False)
        svc = ImageService(site_config=_test_sc())
        assert svc.pexels_available is False

    def test_pexels_base_url_set(self):
        svc = ImageService(site_config=_test_sc())
        assert "pexels.com" in svc.pexels_base_url

    def test_holds_no_in_process_generation_state(self):
        """The image-gen server owns the model; the service loads and tracks
        nothing. These flags described the retired in-process diffusers path
        and read False/None on every deployment. Stage-test fakes that still
        carried them could not catch a stage gating on them again."""
        svc = ImageService(site_config=_test_sc())
        for attr in (
            "_gen_pipe", "_active_model", "gen_available", "gen_initialized",
            "use_device", "get_active_model", "_initialize_model",
            "_initialize_image_gen", "_generate_image_sync",
        ):
            assert not hasattr(svc, attr), f"ImageService.{attr} is back"

    def test_search_cache_starts_empty(self):
        svc = ImageService(site_config=_test_sc())
        assert svc.search_cache == {}


# ---------------------------------------------------------------------------
# search_featured_image
# ---------------------------------------------------------------------------


class TestSearchFeaturedImage:
    @pytest.mark.asyncio
    async def test_returns_none_when_no_api_key(self, monkeypatch):
        monkeypatch.delenv("PEXELS_API_KEY", raising=False)
        svc = ImageService(site_config=_test_sc())
        svc.pexels_api_key = ""
        svc.pexels_available = False
        svc._pexels_key_checked_db = True  # prevent DB lookup
        result = await svc.search_featured_image("AI")
        assert result is None

    @pytest.mark.asyncio
    async def test_returns_image_metadata_on_success(self):
        svc = make_image_service_with_key()
        resp = make_mock_httpx_response({"photos": [SAMPLE_PHOTO]})
        with patch("httpx.AsyncClient") as mock_cls:
            mock_ctx = AsyncMock()
            mock_ctx.__aenter__ = AsyncMock(return_value=mock_ctx)
            mock_ctx.__aexit__ = AsyncMock(return_value=False)
            mock_ctx.get = AsyncMock(return_value=resp)
            mock_cls.return_value = mock_ctx

            result = await svc.search_featured_image("nature")

        assert result is not None
        assert isinstance(result, FeaturedImageMetadata)
        assert result.url == SAMPLE_PHOTO["src"]["large"]

    @pytest.mark.asyncio
    async def test_returns_none_when_no_photos_found(self):
        svc = make_image_service_with_key()
        resp = make_mock_httpx_response({"photos": []})
        with patch("httpx.AsyncClient") as mock_cls:
            mock_ctx = AsyncMock()
            mock_ctx.__aenter__ = AsyncMock(return_value=mock_ctx)
            mock_ctx.__aexit__ = AsyncMock(return_value=False)
            mock_ctx.get = AsyncMock(return_value=resp)
            mock_cls.return_value = mock_ctx

            result = await svc.search_featured_image("very_obscure_topic_xyz")

        assert result is None

    @pytest.mark.asyncio
    async def test_excludes_person_keywords(self):
        svc = make_image_service_with_key()
        make_mock_httpx_response({"photos": [SAMPLE_PHOTO]})
        captured_queries = []

        async def capture_pexels_search(query, **kwargs):
            captured_queries.append(query)
            return []

        with patch.object(svc, "_pexels_search", side_effect=capture_pexels_search):
            await svc.search_featured_image("AI", keywords=["portrait", "people", "technology"])

        # "portrait" and "people" should be excluded; "technology" should be included
        assert not any("portrait" in q for q in captured_queries)
        assert not any("people" in q for q in captured_queries)


# ---------------------------------------------------------------------------
# get_images_for_gallery
# ---------------------------------------------------------------------------


class TestGetImagesForGallery:
    @pytest.mark.asyncio
    async def test_returns_empty_list_without_api_key(self, monkeypatch):
        monkeypatch.delenv("PEXELS_API_KEY", raising=False)
        svc = ImageService(site_config=_test_sc())
        svc.pexels_api_key = ""
        svc.pexels_available = False
        svc._pexels_key_checked_db = True  # prevent DB lookup
        result = await svc.get_images_for_gallery("AI")
        assert result == []

    @pytest.mark.asyncio
    async def test_returns_images_up_to_count(self):
        svc = make_image_service_with_key()
        # Two photos returned by pexels search
        photos = [SAMPLE_PHOTO, {**SAMPLE_PHOTO, "src": {"large": "url2", "small": "url2s"}}]
        resp = make_mock_httpx_response({"photos": photos})
        with patch("httpx.AsyncClient") as mock_cls:
            mock_ctx = AsyncMock()
            mock_ctx.__aenter__ = AsyncMock(return_value=mock_ctx)
            mock_ctx.__aexit__ = AsyncMock(return_value=False)
            mock_ctx.get = AsyncMock(return_value=resp)
            mock_cls.return_value = mock_ctx

            result = await svc.get_images_for_gallery("nature", count=2)

        assert len(result) == 2

    @pytest.mark.asyncio
    async def test_returns_list_on_api_error(self):
        svc = make_image_service_with_key()
        with patch.object(svc, "_pexels_search", side_effect=Exception("API down")):
            result = await svc.get_images_for_gallery("AI")
        assert isinstance(result, list)


# ---------------------------------------------------------------------------
# _pexels_search
# ---------------------------------------------------------------------------


class TestPexelsSearch:
    @pytest.mark.asyncio
    async def test_returns_empty_list_without_key(self, monkeypatch):
        monkeypatch.delenv("PEXELS_API_KEY", raising=False)
        svc = ImageService(site_config=_test_sc())
        result = await svc._pexels_search("AI")
        assert result == []

    @pytest.mark.asyncio
    async def test_maps_photos_to_metadata(self):
        svc = make_image_service_with_key()
        resp = make_mock_httpx_response({"photos": [SAMPLE_PHOTO]})
        with patch("httpx.AsyncClient") as mock_cls:
            mock_ctx = AsyncMock()
            mock_ctx.__aenter__ = AsyncMock(return_value=mock_ctx)
            mock_ctx.__aexit__ = AsyncMock(return_value=False)
            mock_ctx.get = AsyncMock(return_value=resp)
            mock_cls.return_value = mock_ctx

            result = await svc._pexels_search("nature")

        assert len(result) == 1
        img = result[0]
        assert img.photographer == "Jane Doe"
        assert img.width == 1920
        assert img.height == 1080

    @pytest.mark.asyncio
    async def test_source_is_pexels(self):
        svc = make_image_service_with_key()
        resp = make_mock_httpx_response({"photos": [SAMPLE_PHOTO]})
        with patch("httpx.AsyncClient") as mock_cls:
            mock_ctx = AsyncMock()
            mock_ctx.__aenter__ = AsyncMock(return_value=mock_ctx)
            mock_ctx.__aexit__ = AsyncMock(return_value=False)
            mock_ctx.get = AsyncMock(return_value=resp)
            mock_cls.return_value = mock_ctx

            result = await svc._pexels_search("nature")

        assert result[0].source == "pexels"


# ---------------------------------------------------------------------------
# generate_image_markdown / cache helpers
# ---------------------------------------------------------------------------


class TestImageServiceUtils:
    def test_generate_image_markdown_delegates_to_metadata(self):
        svc = ImageService(site_config=_test_sc())
        meta = FeaturedImageMetadata(url="https://example.com/photo.jpg", photographer="John")
        md = svc.generate_image_markdown(meta, caption="Custom caption")
        assert "Custom caption" in md
        assert "example.com/photo.jpg" in md

    def test_cache_get_returns_none_when_empty(self):
        svc = ImageService(site_config=_test_sc())
        assert svc.get_search_cache("any_query") is None

    def test_cache_set_and_get(self):
        svc = ImageService(site_config=_test_sc())
        meta = FeaturedImageMetadata(url="https://example.com/photo.jpg")
        svc.set_search_cache("nature", [meta])
        cached = svc.get_search_cache("nature")
        assert cached is not None
        assert len(cached) == 1
        assert cached[0].url == "https://example.com/photo.jpg"


# ---------------------------------------------------------------------------
# get_image_service factory
# ---------------------------------------------------------------------------


class TestGetImageServiceFactory:
    def test_returns_image_service_instance(self):
        svc = get_image_service(site_config=_test_sc())
        assert isinstance(svc, ImageService)

    def test_returns_fresh_instance_each_time(self):
        s1 = get_image_service(site_config=_test_sc())
        s2 = get_image_service(site_config=_test_sc())
        assert s1 is not s2


# ---------------------------------------------------------------------------
# ImageModel enum: deprecated, kept only as the type of the ignored model=
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestImageModelEnum:
    """``ImageModel`` outlived the worker's model registry for one reason: a
    caller passing ``model=ImageModel.X`` must keep importing and running
    through the deprecation window. So its members stay exactly as they were;
    removing or renaming one breaks such a caller at import time."""

    def test_members_are_unchanged(self):
        assert {m.name: m.value for m in ImageModel} == {
            "SDXL_BASE": "sdxl_base",
            "SDXL_LIGHTNING": "sdxl_lightning",
            "FLUX_SCHNELL": "flux_schnell",
            "Z_IMAGE_TURBO": "z_image_turbo",
        }

    def test_is_str_enum(self):
        # ImageModel inherits from str, so members are valid strings
        assert isinstance(ImageModel.SDXL_BASE, str)
        assert ImageModel.SDXL_LIGHTNING == "sdxl_lightning"

    def test_construct_from_value(self):
        assert ImageModel("sdxl_base") is ImageModel.SDXL_BASE
        assert ImageModel("flux_schnell") is ImageModel.FLUX_SCHNELL

    def test_invalid_value_raises(self):
        with pytest.raises(ValueError, match="nonexistent_model"):
            ImageModel("nonexistent_model")


@pytest.mark.unit
class TestRetiredModelRegistry:
    """The worker's image-model registry described the retired in-process
    diffusers path and had no production reader. It is gone, and so is the
    default resolver for the retired ``image_model`` setting. The image-gen
    server's REGISTRY (scripts/image-gen-server.py) is the only model list."""

    @pytest.mark.parametrize(
        "name",
        ["IMAGE_MODEL_REGISTRY", "ImageModelConfig", "get_default_image_model"],
    )
    def test_registry_names_are_gone_from_image_service(self, name):
        assert not hasattr(image_service_mod, name), (
            f"image_service.{name} is back. The worker renders nothing itself, "
            "so a worker-side model registry can only drift from the server's "
            "REGISTRY. The last one listed flux_schnell, which the server "
            "cannot render, and lacked the server's sdxl_turbo."
        )

    def test_list_available_models_is_gone(self):
        assert not hasattr(ImageService, "list_available_models")

    def test_registry_module_is_gone(self):
        import importlib.util

        assert importlib.util.find_spec(
            "poindexter.services.image_providers._image_models"
        ) is None


# ---------------------------------------------------------------------------
# generate_image / _generate_image_impl — the image-gen HTTP render path
# ---------------------------------------------------------------------------
#
# The image-gen HTTP server is the only render path: the worker image installs
# no diffusers, and the in-process fallback that used to run after a failed
# request is gone. So every failure maps to one of the ImageGenOutcome tokens
# for the server path (server_error / bad_response / write_failed), each
# carrying the diagnosis the operator acts on. The gpu_busy token and the
# GPU-lock wrapper are covered in test_image_service_vram_guard.py.


def _response(
    status_code: int = 200,
    content_type: str = "application/json",
    *,
    payload: Any = None,
    json_error: Exception | None = None,
    content: bytes = b"",
    text: str = "",
    extra_headers: dict[str, str] | None = None,
) -> MagicMock:
    resp = MagicMock()
    resp.status_code = status_code
    resp.headers = {"content-type": content_type, **(extra_headers or {})}
    resp.content = content
    resp.text = text
    if json_error is not None:
        resp.json = MagicMock(side_effect=json_error)
    else:
        resp.json = MagicMock(return_value=payload)
    return resp


def _client(
    *,
    post: MagicMock | None = None,
    post_error: Exception | None = None,
    get: MagicMock | None = None,
) -> AsyncMock:
    client = AsyncMock()
    client.__aenter__ = AsyncMock(return_value=client)
    client.__aexit__ = AsyncMock(return_value=False)
    if post_error is not None:
        client.post = AsyncMock(side_effect=post_error)
    else:
        client.post = AsyncMock(return_value=post)
    client.get = AsyncMock(return_value=get)
    return client


async def _render(client: AsyncMock, output_path: str, **kwargs: Any):
    svc = ImageService(site_config=_test_sc())
    with patch("httpx.AsyncClient", return_value=client):
        return await svc._generate_image_impl("a cat", output_path, **kwargs)


class TestGenerateImage:
    """The image-gen server is the only render path; each failure names why."""

    @pytest.mark.asyncio
    async def test_host_image_gen_server_happy_path(self, tmp_path):
        """Legacy server shape: 200 with raw image bytes -> file written + True."""
        svc = ImageService(site_config=_test_sc())

        png_bytes = b"\x89PNG fake image data"
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.headers = {"content-type": "image/png", "X-Elapsed-Seconds": "1.5"}
        mock_resp.content = png_bytes

        mock_client = AsyncMock()
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)
        mock_client.post = AsyncMock(return_value=mock_resp)

        output_path = str(tmp_path / "out.png")

        with patch("httpx.AsyncClient", return_value=mock_client):
            result = await svc.generate_image(
                prompt="cat in space",
                output_path=output_path,
                negative_prompt="ugly",
            )

        assert result is True
        from pathlib import Path as _P
        assert _P(output_path).exists()
        assert _P(output_path).read_bytes() == png_bytes

    @pytest.mark.asyncio
    async def test_json_response_fetches_the_image_and_writes_it(self, tmp_path):
        """The live server shape: 200 JSON naming the file, then GET /images/<name>.

        The worker does not share the server's volume, so the bytes must come
        over the second request (glad-labs-stack#334).
        """
        png = b"\x89PNG rendered by the server"
        client = _client(
            post=_response(payload={"filename": "img_ab12cd34.png", "generation_time_ms": 4210}),
            get=_response(200, "image/png", content=png),
        )
        out = tmp_path / "out.png"

        outcome = await _render(client, str(out))

        assert outcome.ok is True
        assert out.read_bytes() == png
        fetched = client.get.await_args.args[0]
        assert fetched.endswith("/images/img_ab12cd34.png"), fetched

    @pytest.mark.asyncio
    async def test_server_non_200_is_a_server_error(self, tmp_path):
        """Non-200 -> server_error carrying the status and the server's own body."""
        client = _client(post=_response(500, "text/plain", text="internal error"))

        outcome = await _render(client, str(tmp_path / "x.png"))

        assert outcome.ok is False
        assert outcome.reason == "server_error"
        assert "HTTP 500" in outcome.message
        assert "internal error" in outcome.message
        assert not (tmp_path / "x.png").exists()

    @pytest.mark.asyncio
    async def test_unreachable_server_is_a_server_error(self, tmp_path):
        """No response at all -> server_error naming the exception type."""
        client = _client(post_error=RuntimeError("connection refused"))

        outcome = await _render(client, str(tmp_path / "x.png"))

        assert outcome.reason == "server_error"
        assert "unreachable (RuntimeError)" in outcome.message

    @pytest.mark.asyncio
    async def test_unexpected_content_type_is_a_bad_response(self, tmp_path):
        """200 with neither JSON nor an image is the server answering wrongly,
        not an HTTP error. It used to be labelled "returned HTTP 200" under
        server_error."""
        client = _client(post=_response(200, "text/html", text="<html>error</html>"))

        outcome = await _render(client, str(tmp_path / "x.png"))

        assert outcome.reason == "bad_response"
        assert "text/html" in outcome.message

    @pytest.mark.asyncio
    async def test_unparseable_json_is_a_bad_response(self, tmp_path):
        """A 200 whose JSON body will not parse used to fall into the transport
        ``except`` and read "image-gen server unreachable (JSONDecodeError)",
        which sends the operator after a server that answered."""
        client = _client(
            post=_response(json_error=ValueError("Expecting value"), text="{trunc"),
        )

        outcome = await _render(client, str(tmp_path / "x.png"))

        assert outcome.reason == "bad_response"
        assert "unparseable JSON" in outcome.message
        assert "unreachable" not in outcome.message

    @pytest.mark.parametrize("payload", [{}, {"filename": ""}, ["img.png"]], ids=["missing", "empty", "not-a-dict"])
    @pytest.mark.asyncio
    async def test_json_without_a_filename_is_a_bad_response(self, tmp_path, payload):
        client = _client(post=_response(payload=payload))

        outcome = await _render(client, str(tmp_path / "x.png"))

        assert outcome.reason == "bad_response"
        assert "no filename" in outcome.message
        client.get.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_failed_image_fetch_is_a_bad_response(self, tmp_path):
        client = _client(
            post=_response(payload={"filename": "img_gone.png"}),
            get=_response(404, "text/plain", text="not found"),
        )

        outcome = await _render(client, str(tmp_path / "x.png"))

        assert outcome.reason == "bad_response"
        assert "HTTP 404" in outcome.message

    @pytest.mark.asyncio
    async def test_local_write_failure_is_write_failed_not_a_server_error(self, tmp_path):
        """The server rendered, the worker could not save it. Calling that a
        server failure (it used to read "unreachable (FileNotFoundError)")
        sends the operator to a healthy container. The detail names the type
        only: it reaches an HTTP body, and the error message carries the path."""
        target = tmp_path / "no-such-dir" / "x.png"
        client = _client(post=_response(200, "image/png", content=b"\x89PNG"))

        outcome = await _render(client, str(target))

        assert outcome.ok is False
        assert outcome.reason == "write_failed"
        assert "FileNotFoundError" in outcome.message
        assert "no-such-dir" not in outcome.message

    @pytest.mark.asyncio
    async def test_request_body_omits_unset_overrides(self, tmp_path):
        """Unset steps / guidance / task_id stay out of the request, so the
        server's per-model registry decides (z_image_turbo wants 9 steps / CFG 0)."""
        client = _client(post=_response(200, "image/png", content=b"\x89PNG"))

        await _render(client, str(tmp_path / "x.png"), negative_prompt="blurry")

        body = client.post.await_args.kwargs["json"]
        assert body == {"prompt": "a cat", "negative_prompt": "blurry"}

    @pytest.mark.asyncio
    async def test_request_body_carries_explicit_overrides_and_task_id(self, tmp_path):
        """task_id goes to the server, which stamps it on its
        image_ocr_gate_result audit row, the field the pipeline's own render
        paths already send."""
        client = _client(post=_response(200, "image/png", content=b"\x89PNG"))

        await _render(
            client, str(tmp_path / "x.png"),
            num_inference_steps=9, guidance_scale=0.0, task_id="task-123",
        )

        body = client.post.await_args.kwargs["json"]
        assert body["steps"] == 9
        assert body["guidance_scale"] == 0.0
        assert body["task_id"] == "task-123"


# ---------------------------------------------------------------------------
# model= — deprecated, accepted, ignored, and said so
# ---------------------------------------------------------------------------


@asynccontextmanager
async def _noop_gpu_lock(*_a, **_kw):
    yield


@asynccontextmanager
async def _busy_gpu_lock(*_a, **_kw):
    raise GpuBusyError("eta_exceeds_budget", 412.0)
    yield  # pragma: no cover — unreachable, keeps this a generator


class TestDeprecatedModelArgument:
    """The server renders app_settings.image_generation_model and takes no
    per-request model, so ``model=`` cannot be honoured. The public entry points
    keep accepting it through the deprecation window and say so, instead of
    either breaking the caller or dropping the argument silently."""

    @pytest.mark.asyncio
    async def test_warns_and_renders_the_configured_model(self, caplog):
        svc = ImageService(site_config=_test_sc())
        impl = AsyncMock(return_value=ImageGenOutcome(True))
        with patch.object(gpu_scheduler_mod, "gpu", SimpleNamespace(lock=_noop_gpu_lock)), \
                patch.object(ImageService, "_generate_image_impl", impl), \
                caplog.at_level(logging.WARNING):
            outcome = await svc.generate_image_result(
                "a cat", "/tmp/x.png", model=ImageModel.FLUX_SCHNELL,
            )

        assert outcome.ok is True
        impl.assert_awaited_once()
        assert "model" not in impl.await_args.kwargs, (
            "model= reached the renderer; the server cannot take it"
        )
        assert "model=flux_schnell is deprecated and ignored" in caplog.text

    @pytest.mark.asyncio
    async def test_bool_wrapper_warns_too(self, caplog):
        svc = ImageService(site_config=_test_sc())
        with patch.object(gpu_scheduler_mod, "gpu", SimpleNamespace(lock=_noop_gpu_lock)), \
                patch.object(
                    ImageService, "_generate_image_impl",
                    AsyncMock(return_value=ImageGenOutcome(True)),
                ), caplog.at_level(logging.WARNING):
            ok = await svc.generate_image("a cat", "/tmp/x.png", model=ImageModel.SDXL_BASE)

        assert ok is True
        assert "model=sdxl_base is deprecated and ignored" in caplog.text

    @pytest.mark.asyncio
    async def test_warns_before_the_gpu_wait(self, caplog):
        """A caller refused the GPU still learns its argument does nothing."""
        svc = ImageService(site_config=_test_sc())
        impl = AsyncMock(return_value=ImageGenOutcome(True))
        with patch.object(gpu_scheduler_mod, "gpu", SimpleNamespace(lock=_busy_gpu_lock)), \
                patch.object(ImageService, "_generate_image_impl", impl), \
                caplog.at_level(logging.WARNING):
            outcome = await svc.generate_image_result(
                "a cat", "/tmp/x.png", model=ImageModel.Z_IMAGE_TURBO,
            )

        assert outcome.reason == "gpu_busy"
        impl.assert_not_awaited()
        assert "model=z_image_turbo is deprecated and ignored" in caplog.text

    @pytest.mark.asyncio
    async def test_no_warning_without_the_argument(self, caplog):
        svc = ImageService(site_config=_test_sc())
        with patch.object(gpu_scheduler_mod, "gpu", SimpleNamespace(lock=_noop_gpu_lock)), \
                patch.object(
                    ImageService, "_generate_image_impl",
                    AsyncMock(return_value=ImageGenOutcome(True)),
                ), caplog.at_level(logging.WARNING):
            await svc.generate_image_result("a cat", "/tmp/x.png")

        assert "deprecated" not in caplog.text

    def test_public_signatures_still_accept_model(self):
        """Backward compatibility: removing the parameter breaks every caller
        that passes it, which is the deprecation window's whole point."""
        for method in (ImageService.generate_image, ImageService.generate_image_result):
            param = inspect.signature(method).parameters.get("model")
            assert param is not None, f"{method.__name__} lost its model= parameter"
            assert param.default is None

    def test_renderer_takes_no_model(self):
        """The private render path never sees a model: the server picks it."""
        assert "model" not in inspect.signature(ImageService._generate_image_impl).parameters


# ---------------------------------------------------------------------------
# _ensure_pexels_key — DB-first key loading
# ---------------------------------------------------------------------------


class TestEnsurePexelsKey:
    """Tests for the SiteConfig-first Pexels API key loader.

    Post-#381 refactor, ``_ensure_pexels_key`` resolves through the
    canonical Phase H DI seam — ``SiteConfig.get_secret`` — instead of
    fishing for the legacy DI-container "database" registration that
    was missed during Phase H cutover. Three paths:

    1. Already-checked flag → no-op
    2. SiteConfig has no DB pool → RuntimeError (loud failure per
       feedback_no_silent_defaults — we cannot tell "key intentionally
       unset" from "lookup broken")
    3. SiteConfig.get_secret returns a value → set state and continue
       (or empty value → leave unavailable, log info)
    """

    def _make_site_config(self, secret_value: str | None) -> Any:
        """Build a SiteConfig stub with a mock pool + ``get_secret``.

        Mirrors what the lifespan-loaded singleton looks like to
        ImageService — non-None ``_pool`` (so the loud-failure guard
        passes) plus an async ``get_secret`` returning ``secret_value``.
        """
        from poindexter.services.site_config import SiteConfig

        cfg = SiteConfig()
        cfg._pool = MagicMock()  # non-None — passes the loud-failure guard

        async def _fake_get_secret(key: str, default: str = "") -> str:
            return secret_value if secret_value is not None else default

        cfg.get_secret = _fake_get_secret  # type: ignore[assignment]
        return cfg

    @pytest.mark.asyncio
    async def test_already_checked_noop(self):
        cfg = self._make_site_config("never-fetched")
        svc = ImageService(site_config=cfg)
        svc.pexels_api_key = "existing-key"
        svc._pexels_key_checked_db = True

        # Should not call get_secret at all.
        cfg.get_secret = AsyncMock(side_effect=AssertionError("must not run"))
        await svc._ensure_pexels_key()

    @pytest.mark.asyncio
    async def test_loads_from_site_config_get_secret(self):
        cfg = self._make_site_config("decrypted-key")
        svc = ImageService(site_config=cfg)
        svc._pexels_key_checked_db = False

        await svc._ensure_pexels_key()

        assert svc.pexels_api_key == "decrypted-key"
        assert svc.pexels_available is True
        assert svc.pexels_headers == {"Authorization": "decrypted-key"}
        assert svc._pexels_key_checked_db is True

    @pytest.mark.asyncio
    async def test_no_pool_raises_loud(self):
        """SiteConfig with no DB pool → loud RuntimeError, not silent unavailable.

        feedback_no_silent_defaults: we cannot distinguish "key
        intentionally unset" from "lookup mechanism broken" without a
        DB pool, so refuse to continue with pexels_available=False.
        """
        from poindexter.services.site_config import SiteConfig

        cfg = SiteConfig()  # no pool — fresh test instance
        svc = ImageService(site_config=cfg)
        svc._pexels_key_checked_db = False

        with pytest.raises(RuntimeError, match="SiteConfig has no DB pool"):
            await svc._ensure_pexels_key()

    @pytest.mark.asyncio
    async def test_db_returns_empty_leaves_unavailable(self):
        """Empty value is a legitimate state — Pexels is a fallback source."""
        cfg = self._make_site_config("")
        svc = ImageService(site_config=cfg)
        svc._pexels_key_checked_db = False

        await svc._ensure_pexels_key()

        assert svc.pexels_api_key is None
        assert svc.pexels_available is False
        assert svc._pexels_key_checked_db is True

    @pytest.mark.asyncio
    async def test_get_secret_exception_raises_loud(self):
        """A DB error mid-lookup must raise, not silently fall back."""
        from poindexter.services.site_config import SiteConfig

        cfg = SiteConfig()
        cfg._pool = MagicMock()  # passes pool guard

        async def _boom(key: str, default: str = "") -> str:
            raise RuntimeError("db connection lost")

        cfg.get_secret = _boom  # type: ignore[assignment]

        svc = ImageService(site_config=cfg)
        svc._pexels_key_checked_db = False

        with pytest.raises(RuntimeError, match="pexels_api_key lookup failed"):
            await svc._ensure_pexels_key()


# ---------------------------------------------------------------------------
# Regression test for poindexter#381 — pexels resolution does NOT depend on
# the legacy `services.container.get_service("database")` registration.
# ---------------------------------------------------------------------------


class TestPexelsResolutionViaSiteConfigDI:
    """Regression for poindexter#381.

    Pre-fix: ``_ensure_pexels_key`` reached for ``get_service("database")``
    in the global ``service_container``. main.py registers that service
    under the key ``database_service`` (not ``database``), so the lookup
    silently returned None and the worker emitted
    "DatabaseService not registered in DI container — pexels_api_key
    cannot be loaded." for every pipeline run.

    Fix: route through the injected ``SiteConfig`` instance, which is
    threaded from the lifespan-loaded ``app.state.site_config`` /
    ``context['site_config']`` into the ImageService ctor.
    """

    @pytest.mark.asyncio
    async def test_pexels_available_when_only_site_config_is_wired(self):
        """Fresh ImageService + SiteConfig (no global container mutation)
        → pexels_api_key resolves and pexels_available flips to True.

        Critically, this test does NOT touch ``services.container`` at
        all. If the implementation ever re-introduces a global lookup,
        this test still passes — but the loud guard in
        ``_ensure_pexels_key`` ensures the warning that triggered #381
        cannot silently re-emerge.
        """
        from poindexter.services.image_service import ImageService, get_image_service
        from poindexter.services.site_config import SiteConfig

        cfg = SiteConfig()
        cfg._pool = MagicMock()  # non-None: passes the loud-failure guard

        async def _fake_get_secret(key: str, default: str = "") -> str:
            assert key == "pexels_api_key"
            return "stress-test-key-381"

        cfg.get_secret = _fake_get_secret  # type: ignore[assignment]

        # Construct via factory + ctor — both must accept site_config
        svc_via_factory = get_image_service(site_config=cfg)
        svc_via_ctor = ImageService(site_config=cfg)

        for svc in (svc_via_factory, svc_via_ctor):
            assert svc.pexels_available is False  # before key resolution
            await svc._ensure_pexels_key()
            assert svc.pexels_available is True
            assert svc.pexels_api_key == "stress-test-key-381"
            assert svc.pexels_headers == {"Authorization": "stress-test-key-381"}
