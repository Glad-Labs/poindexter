"""
Unit tests for services/image_service.py

Tests FeaturedImageMetadata (to_dict, to_markdown), ImageService initialization,
search_featured_image, get_images_for_gallery, _pexels_search (mocked httpx),
generate_image_markdown, optimize_image_for_web, cache helpers, and factory.
Heavy GPU/SDXL paths are not exercised; they are tested via flag checks only.
"""

from contextlib import asynccontextmanager
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from services.image_service import (
    IMAGE_MODEL_REGISTRY,
    FeaturedImageMetadata,
    ImageModel,
    ImageModelConfig,
    ImageService,
    get_default_image_model,
    get_image_service,
)

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
    svc = ImageService()
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
        return ImageService()


# ---------------------------------------------------------------------------
# FeaturedImageMetadata
# ---------------------------------------------------------------------------


class TestFeaturedImageMetadata:
    def _make_meta(self, **kwargs) -> FeaturedImageMetadata:
        defaults = dict(
            url="https://example.com/photo.jpg",
            thumbnail="https://example.com/thumb.jpg",
            photographer="John Smith",
            photographer_url="https://example.com/@john",
            width=1920,
            height=1080,
            alt_text="A photo",
            caption="Photo caption",
            source="pexels",
            search_query="nature",
        )
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
        svc = ImageService()
        assert svc.pexels_available is False

    def test_pexels_base_url_set(self):
        svc = ImageService()
        assert "pexels.com" in svc.pexels_base_url

    def test_sdxl_not_initialized_at_startup(self):
        svc = ImageService()
        # Models are lazily initialized only when generate_image() is called
        assert svc.sdxl_initialized is False
        assert svc._gen_pipe is None
        assert svc._active_model is None

    def test_search_cache_starts_empty(self):
        svc = ImageService()
        assert svc.search_cache == {}


# ---------------------------------------------------------------------------
# search_featured_image
# ---------------------------------------------------------------------------


class TestSearchFeaturedImage:
    @pytest.mark.asyncio
    async def test_returns_none_when_no_api_key(self, monkeypatch):
        monkeypatch.delenv("PEXELS_API_KEY", raising=False)
        svc = ImageService()
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
        resp = make_mock_httpx_response({"photos": [SAMPLE_PHOTO]})
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
        svc = ImageService()
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
        svc = ImageService()
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
# generate_image_markdown / optimize_image_for_web / cache helpers
# ---------------------------------------------------------------------------


class TestImageServiceUtils:
    def test_generate_image_markdown_delegates_to_metadata(self):
        svc = ImageService()
        meta = FeaturedImageMetadata(url="https://example.com/photo.jpg", photographer="John")
        md = svc.generate_image_markdown(meta, caption="Custom caption")
        assert "Custom caption" in md
        assert "example.com/photo.jpg" in md

    @pytest.mark.asyncio
    async def test_optimize_image_returns_original_url(self):
        svc = ImageService()
        result = await svc.optimize_image_for_web("https://example.com/image.jpg")
        assert result is not None
        assert result["url"] == "https://example.com/image.jpg"
        assert result["optimized"] is False

    def test_cache_get_returns_none_when_empty(self):
        svc = ImageService()
        assert svc.get_search_cache("any_query") is None

    def test_cache_set_and_get(self):
        svc = ImageService()
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
        svc = get_image_service()
        assert isinstance(svc, ImageService)

    def test_returns_fresh_instance_each_time(self):
        s1 = get_image_service()
        s2 = get_image_service()
        assert s1 is not s2


# ---------------------------------------------------------------------------
# ImageModel enum
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestImageModelEnum:
    def test_has_three_members(self):
        assert len(ImageModel) == 3

    def test_sdxl_base_value(self):
        assert ImageModel.SDXL_BASE.value == "sdxl_base"

    def test_sdxl_lightning_value(self):
        assert ImageModel.SDXL_LIGHTNING.value == "sdxl_lightning"

    def test_flux_schnell_value(self):
        assert ImageModel.FLUX_SCHNELL.value == "flux_schnell"

    def test_is_str_enum(self):
        # ImageModel inherits from str, so members are valid strings
        assert isinstance(ImageModel.SDXL_BASE, str)
        assert ImageModel.SDXL_LIGHTNING == "sdxl_lightning"

    def test_construct_from_value(self):
        assert ImageModel("sdxl_base") is ImageModel.SDXL_BASE
        assert ImageModel("flux_schnell") is ImageModel.FLUX_SCHNELL

    def test_invalid_value_raises(self):
        with pytest.raises(ValueError):
            ImageModel("nonexistent_model")


# ---------------------------------------------------------------------------
# ImageModelConfig dataclass
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestImageModelConfig:
    def test_frozen_cannot_mutate(self):
        cfg = ImageModelConfig(
            model_id="test/model",
            display_name="Test",
            default_steps=10,
            default_guidance_scale=7.0,
            pipeline_class="diffusers.SomePipeline",
        )
        with pytest.raises(AttributeError):
            cfg.model_id = "other/model"  # type: ignore[misc]

    def test_default_optional_fields(self):
        cfg = ImageModelConfig(
            model_id="test/model",
            display_name="Test",
            default_steps=10,
            default_guidance_scale=7.0,
            pipeline_class="diffusers.SomePipeline",
        )
        assert cfg.lora_repo is None
        assert cfg.lora_weight_name is None
        assert cfg.scheduler_override is None
        assert cfg.scheduler_kwargs is None
        assert cfg.torch_dtype_str == "float16"
        assert cfg.vram_gb == 6.0
        assert cfg.notes == ""

    def test_explicit_fields_stored(self):
        cfg = ImageModelConfig(
            model_id="org/model-name",
            display_name="My Model",
            default_steps=30,
            default_guidance_scale=7.5,
            pipeline_class="diffusers.StableDiffusionXLPipeline",
            lora_repo="ByteDance/SDXL-Lightning",
            lora_weight_name="weights.safetensors",
            scheduler_override="EulerDiscreteScheduler",
            scheduler_kwargs={"timestep_spacing": "trailing"},
            torch_dtype_str="bfloat16",
            vram_gb=12.0,
            notes="Test note",
        )
        assert cfg.model_id == "org/model-name"
        assert cfg.display_name == "My Model"
        assert cfg.default_steps == 30
        assert cfg.default_guidance_scale == 7.5
        assert cfg.lora_repo == "ByteDance/SDXL-Lightning"
        assert cfg.scheduler_kwargs == {"timestep_spacing": "trailing"}
        assert cfg.torch_dtype_str == "bfloat16"
        assert cfg.vram_gb == 12.0


# ---------------------------------------------------------------------------
# IMAGE_MODEL_REGISTRY
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestImageModelRegistry:
    def test_contains_all_three_models(self):
        assert set(IMAGE_MODEL_REGISTRY.keys()) == {
            ImageModel.SDXL_BASE,
            ImageModel.SDXL_LIGHTNING,
            ImageModel.FLUX_SCHNELL,
        }

    def test_all_entries_are_image_model_config(self):
        for model, cfg in IMAGE_MODEL_REGISTRY.items():
            assert isinstance(cfg, ImageModelConfig), f"{model} value is not ImageModelConfig"

    def test_all_entries_have_required_fields(self):
        for model, cfg in IMAGE_MODEL_REGISTRY.items():
            assert cfg.model_id, f"{model} missing model_id"
            assert cfg.display_name, f"{model} missing display_name"
            assert cfg.default_steps > 0, f"{model} has non-positive default_steps"
            assert cfg.default_guidance_scale >= 0, f"{model} has negative guidance_scale"
            assert cfg.pipeline_class.startswith(
                "diffusers."
            ), f"{model} pipeline_class should start with 'diffusers.'"
            assert cfg.vram_gb > 0, f"{model} has non-positive vram_gb"

    def test_sdxl_base_config(self):
        cfg = IMAGE_MODEL_REGISTRY[ImageModel.SDXL_BASE]
        assert cfg.model_id == "stabilityai/stable-diffusion-xl-base-1.0"
        assert cfg.default_steps == 30
        assert cfg.lora_repo is None

    def test_sdxl_lightning_config(self):
        cfg = IMAGE_MODEL_REGISTRY[ImageModel.SDXL_LIGHTNING]
        assert cfg.lora_repo == "ByteDance/SDXL-Lightning"
        assert cfg.lora_weight_name is not None
        assert cfg.scheduler_override == "EulerDiscreteScheduler"
        assert cfg.default_steps == 4
        assert cfg.default_guidance_scale == 0.0

    def test_flux_schnell_config(self):
        cfg = IMAGE_MODEL_REGISTRY[ImageModel.FLUX_SCHNELL]
        assert cfg.model_id == "black-forest-labs/FLUX.1-schnell"
        assert cfg.torch_dtype_str == "bfloat16"
        assert cfg.vram_gb == 12.0
        assert cfg.pipeline_class == "diffusers.FluxPipeline"


# ---------------------------------------------------------------------------
# get_default_image_model()
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestGetDefaultImageModel:
    def test_returns_sdxl_lightning_when_env_not_set(self, monkeypatch):
        monkeypatch.delenv("IMAGE_MODEL", raising=False)
        result = get_default_image_model()
        assert result is ImageModel.SDXL_LIGHTNING

    def test_returns_sdxl_base_from_env(self, monkeypatch):
        monkeypatch.setenv("IMAGE_MODEL", "sdxl_base")
        result = get_default_image_model()
        assert result is ImageModel.SDXL_BASE

    def test_returns_flux_schnell_from_env(self, monkeypatch):
        monkeypatch.setenv("IMAGE_MODEL", "flux_schnell")
        result = get_default_image_model()
        assert result is ImageModel.FLUX_SCHNELL

    def test_returns_sdxl_lightning_from_env(self, monkeypatch):
        monkeypatch.setenv("IMAGE_MODEL", "sdxl_lightning")
        result = get_default_image_model()
        assert result is ImageModel.SDXL_LIGHTNING

    def test_falls_back_on_invalid_env(self, monkeypatch):
        monkeypatch.setenv("IMAGE_MODEL", "nonexistent_model_xyz")
        result = get_default_image_model()
        assert result is ImageModel.SDXL_LIGHTNING

    def test_falls_back_on_empty_string_env(self, monkeypatch):
        monkeypatch.setenv("IMAGE_MODEL", "")
        result = get_default_image_model()
        assert result is ImageModel.SDXL_LIGHTNING


# ---------------------------------------------------------------------------
# _initialize_model()
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestInitializeModel:
    def test_returns_early_when_model_already_loaded(self):
        """When the requested model is already the active model, _initialize_model is a no-op."""
        svc = ImageService()
        svc._active_model = ImageModel.SDXL_BASE
        svc._gen_pipe = MagicMock()  # pretend a pipeline is loaded
        original_pipe = svc._gen_pipe

        svc._initialize_model(ImageModel.SDXL_BASE)

        # Pipeline reference unchanged — no reload happened
        assert svc._gen_pipe is original_pipe

    def test_sets_sdxl_available_false_when_diffusers_unavailable(self):
        svc = ImageService()
        with patch("services.image_service.DIFFUSERS_AVAILABLE", False):
            svc._initialize_model(ImageModel.SDXL_BASE)
        assert svc.sdxl_available is False
        assert svc._gen_pipe is None

    def test_sets_sdxl_available_false_when_torch_unavailable(self):
        svc = ImageService()
        with (
            patch("services.image_service.DIFFUSERS_AVAILABLE", True),
            patch("services.image_service.TORCH_AVAILABLE", False),
        ):
            svc._initialize_model(ImageModel.SDXL_BASE)
        assert svc.sdxl_available is False
        assert svc._gen_pipe is None

    def test_unloads_previous_model_before_loading_new(self):
        """When switching models, _unload_model is called before loading the new one."""
        svc = ImageService()
        svc._gen_pipe = MagicMock()
        svc._active_model = ImageModel.SDXL_BASE

        # DIFFUSERS and TORCH must be True so we get past the prerequisite checks
        # and reach the unload branch. We then let the actual load fail (no real GPU).
        with (
            patch("services.image_service.DIFFUSERS_AVAILABLE", True),
            patch("services.image_service.TORCH_AVAILABLE", True),
            patch.object(svc, "_unload_model") as mock_unload,
            patch.object(svc, "_import_pipeline_class", side_effect=ImportError("no GPU")),
        ):
            svc._initialize_model(ImageModel.FLUX_SCHNELL)
            mock_unload.assert_called_once()

    def test_uses_get_default_when_model_is_none(self):
        svc = ImageService()
        with (
            patch("services.image_service.DIFFUSERS_AVAILABLE", False),
            patch(
                "services.image_service.get_default_image_model",
                return_value=ImageModel.FLUX_SCHNELL,
            ) as mock_default,
        ):
            svc._initialize_model(None)
            mock_default.assert_called_once()


# ---------------------------------------------------------------------------
# _unload_model()
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestUnloadModel:
    def test_clears_pipeline_and_model(self):
        svc = ImageService()
        svc._gen_pipe = MagicMock()
        svc._active_model = ImageModel.SDXL_BASE
        svc.sdxl_available = True

        with patch("services.image_service.TORCH_AVAILABLE", False):
            svc._unload_model()

        assert svc._gen_pipe is None
        assert svc._active_model is None
        assert svc.sdxl_available is False

    def test_noop_when_no_pipeline_loaded(self):
        svc = ImageService()
        assert svc._gen_pipe is None
        with patch("services.image_service.TORCH_AVAILABLE", False):
            svc._unload_model()  # Should not raise
        assert svc._gen_pipe is None
        assert svc._active_model is None
        assert svc.sdxl_available is False

    def test_clears_cuda_cache_when_available(self):
        svc = ImageService()
        svc._gen_pipe = MagicMock()
        svc._active_model = ImageModel.SDXL_LIGHTNING

        mock_torch = MagicMock()
        mock_torch.cuda.is_available.return_value = True

        import services.image_service as img_mod

        original_torch = getattr(img_mod, "torch", None)
        try:
            img_mod.torch = mock_torch
            with patch("services.image_service.TORCH_AVAILABLE", True):
                svc._unload_model()
            mock_torch.cuda.empty_cache.assert_called_once()
        finally:
            if original_torch is not None:
                img_mod.torch = original_torch

    def test_skips_cuda_cache_when_not_available(self):
        svc = ImageService()
        svc._gen_pipe = MagicMock()
        svc._active_model = ImageModel.SDXL_BASE

        mock_torch = MagicMock()
        mock_torch.cuda.is_available.return_value = False

        import services.image_service as img_mod

        original_torch = getattr(img_mod, "torch", None)
        try:
            img_mod.torch = mock_torch
            with patch("services.image_service.TORCH_AVAILABLE", True):
                svc._unload_model()
            mock_torch.cuda.empty_cache.assert_not_called()
        finally:
            if original_torch is not None:
                img_mod.torch = original_torch


# ---------------------------------------------------------------------------
# _import_pipeline_class()
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestImportPipelineClass:
    def test_valid_dotted_path(self):
        # Use a known stdlib class as a stand-in
        cls = ImageService._import_pipeline_class("collections.OrderedDict")
        from collections import OrderedDict

        assert cls is OrderedDict

    def test_another_valid_path(self):
        cls = ImageService._import_pipeline_class("os.path")
        import os.path

        assert cls is os.path

    def test_invalid_path_no_dot(self):
        with pytest.raises(ImportError, match="Invalid pipeline class path"):
            ImageService._import_pipeline_class("nodots")

    def test_nonexistent_module(self):
        with pytest.raises(ModuleNotFoundError):
            ImageService._import_pipeline_class("nonexistent_module_xyz.SomeClass")

    def test_nonexistent_class_in_valid_module(self):
        with pytest.raises(AttributeError):
            ImageService._import_pipeline_class("collections.NonexistentClassXyz")

    def test_with_mock_importlib(self):
        """Verify the method calls importlib.import_module with the module part."""
        mock_module = MagicMock()
        mock_module.MyPipeline = "fake_class"

        with patch("importlib.import_module", return_value=mock_module) as mock_import:
            result = ImageService._import_pipeline_class("diffusers.MyPipeline")

        mock_import.assert_called_once_with("diffusers")
        assert result == "fake_class"


# ---------------------------------------------------------------------------
# generate_image — main public method
# ---------------------------------------------------------------------------


class TestGenerateImage:
    """Coverage for the 3-strategy generate_image method."""

    @pytest.mark.asyncio
    async def test_host_sdxl_server_happy_path(self, tmp_path):
        """Strategy 1: host SDXL server returns image bytes -> file written + True."""
        svc = ImageService()

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
    async def test_host_sdxl_non_200_falls_through_to_local(self, tmp_path):
        """If host SDXL returns 500 and local diffusers unavailable -> False."""
        svc = ImageService()
        svc.sdxl_available = False  # local diffusers not available

        mock_resp = MagicMock()
        mock_resp.status_code = 500
        mock_resp.headers = {"content-type": "text/plain"}
        mock_resp.text = "internal error"
        mock_resp.content = b""

        mock_client = AsyncMock()
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)
        mock_client.post = AsyncMock(return_value=mock_resp)

        with patch("httpx.AsyncClient", return_value=mock_client), \
             patch.object(svc, "_initialize_model"):
            svc.sdxl_initialized = True  # skip the lazy init
            result = await svc.generate_image(
                prompt="x",
                output_path=str(tmp_path / "x.png"),
            )

        assert result is False

    @pytest.mark.asyncio
    async def test_host_sdxl_exception_falls_through_to_local(self, tmp_path):
        """Connection error on host SDXL + diffusers unavailable -> False."""
        svc = ImageService()
        svc.sdxl_available = False

        mock_client = AsyncMock()
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)
        mock_client.post = AsyncMock(side_effect=RuntimeError("connection refused"))

        with patch("httpx.AsyncClient", return_value=mock_client), \
             patch.object(svc, "_initialize_model"):
            svc.sdxl_initialized = True
            result = await svc.generate_image(
                prompt="x", output_path=str(tmp_path / "x.png"),
            )

        assert result is False

    @pytest.mark.asyncio
    async def test_host_sdxl_wrong_content_type_falls_through(self, tmp_path):
        """200 with text/html content-type is treated as failure."""
        svc = ImageService()
        svc.sdxl_available = False

        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.headers = {"content-type": "text/html"}
        mock_resp.text = "<html>error</html>"
        mock_resp.content = b""

        mock_client = AsyncMock()
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)
        mock_client.post = AsyncMock(return_value=mock_resp)

        with patch("httpx.AsyncClient", return_value=mock_client), \
             patch.object(svc, "_initialize_model"):
            svc.sdxl_initialized = True
            result = await svc.generate_image(
                prompt="x", output_path=str(tmp_path / "x.png"),
            )

        assert result is False


# ---------------------------------------------------------------------------
# _ensure_pexels_key — DB-first key loading
# ---------------------------------------------------------------------------


class TestEnsurePexelsKey:
    """Tests for the DB-first (encrypted) Pexels API key loader.

    Post-refactor, ``_ensure_pexels_key`` only has two paths:
    1. Already-checked flag → no-op
    2. Query ``plugins.secrets.get_secret`` via the shared pool from
       ``services.container.get_service("database")``

    No more site_config fallback, no more ``os.getenv("LOCAL_DATABASE_URL")``
    fresh-connection fallback — those contradicted the "DB-first, no
    hardcoded configs" policy.
    """

    @pytest.mark.asyncio
    async def test_already_checked_noop(self):
        svc = ImageService()
        svc.pexels_api_key = "existing-key"
        svc._pexels_key_checked_db = True

        # Should not touch the container at all.
        with patch("services.container.get_service") as mock_get:
            await svc._ensure_pexels_key()
        mock_get.assert_not_called()

    @pytest.mark.asyncio
    async def test_loads_from_db_via_get_secret(self):
        svc = ImageService()
        svc._pexels_key_checked_db = False

        fake_conn = AsyncMock()
        fake_pool_ctx = AsyncMock()
        fake_pool_ctx.__aenter__ = AsyncMock(return_value=fake_conn)
        fake_pool_ctx.__aexit__ = AsyncMock(return_value=False)

        fake_db_service = MagicMock()
        fake_db_service.pool = MagicMock()
        fake_db_service.pool.acquire = MagicMock(return_value=fake_pool_ctx)

        with patch("services.container.get_service", return_value=fake_db_service), \
             patch("plugins.secrets.get_secret", new=AsyncMock(return_value="decrypted-key")):
            await svc._ensure_pexels_key()

        assert svc.pexels_api_key == "decrypted-key"
        assert svc.pexels_available is True
        assert svc.pexels_headers == {"Authorization": "decrypted-key"}
        assert svc._pexels_key_checked_db is True

    @pytest.mark.asyncio
    async def test_no_db_service_sets_unavailable(self):
        svc = ImageService()
        svc._pexels_key_checked_db = False

        with patch("services.container.get_service", return_value=None):
            await svc._ensure_pexels_key()

        assert svc.pexels_api_key is None
        assert svc.pexels_available is False
        assert svc._pexels_key_checked_db is True

    @pytest.mark.asyncio
    async def test_db_returns_empty_leaves_unavailable(self):
        svc = ImageService()
        svc._pexels_key_checked_db = False

        fake_conn = AsyncMock()
        fake_pool_ctx = AsyncMock()
        fake_pool_ctx.__aenter__ = AsyncMock(return_value=fake_conn)
        fake_pool_ctx.__aexit__ = AsyncMock(return_value=False)

        fake_db_service = MagicMock()
        fake_db_service.pool = MagicMock()
        fake_db_service.pool.acquire = MagicMock(return_value=fake_pool_ctx)

        with patch("services.container.get_service", return_value=fake_db_service), \
             patch("plugins.secrets.get_secret", new=AsyncMock(return_value=None)):
            await svc._ensure_pexels_key()

        assert svc.pexels_api_key is None
        assert svc.pexels_available is False


# ---------------------------------------------------------------------------
# get_active_model + list_available_models + optimize_image_for_web
# ---------------------------------------------------------------------------


class TestModelIntrospection:
    def test_get_active_model_none_at_startup(self):
        svc = ImageService()
        # Fresh instance — nothing loaded
        assert svc.get_active_model() is None

    def test_get_active_model_returns_loaded(self):
        svc = ImageService()
        svc._active_model = ImageModel.SDXL_LIGHTNING
        assert svc.get_active_model() == ImageModel.SDXL_LIGHTNING

    def test_list_available_models_returns_dict_with_all_three(self):
        models = ImageService.list_available_models()
        assert isinstance(models, dict)
        # All three from the registry
        for m in IMAGE_MODEL_REGISTRY:
            assert m.value in models

    def test_list_available_models_entries_have_metadata(self):
        models = ImageService.list_available_models()
        for value, meta in models.items():
            assert "display_name" in meta
            assert "default_steps" in meta
            assert "vram_gb" in meta
            assert "notes" in meta


class TestOptimizeImageForWeb:
    @pytest.mark.asyncio
    async def test_returns_placeholder_dict(self):
        svc = ImageService()
        result = await svc.optimize_image_for_web("https://cdn.example.com/img.png")
        assert result is not None
        assert result["url"] == "https://cdn.example.com/img.png"
        assert result["optimized"] is False
        assert "not yet implemented" in result["note"].lower()

    @pytest.mark.asyncio
    async def test_accepts_size_overrides(self):
        svc = ImageService()
        # Just verify it doesn't raise with width/height overrides
        result = await svc.optimize_image_for_web(
            "https://cdn.example.com/x.png",
            max_width=2000,
            max_height=1000,
        )
        assert result is not None
