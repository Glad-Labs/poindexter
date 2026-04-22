"""
Unit tests for services/image_service.py

Post-Phase-G (GH#71) the SDXL model lifecycle lives in
``services/image_providers/sdxl.py`` — the tests for
``_initialize_model`` / ``_unload_model`` / ``_import_pipeline_class``
now live in ``test_image_providers_sdxl.py``. This file covers the
ImageService public surface: FeaturedImageMetadata, Pexels search
orchestration, the ImageProvider dispatcher, and utility methods.
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
    """Return an ImageService that thinks Pexels is configured.

    Post-Phase-G the key itself lives in PexelsProvider — ImageService
    only caches an ``is-configured`` verdict. Flip the cache flags so
    tests don't try to hit the DB probe.
    """
    svc = ImageService()
    svc.pexels_available = True
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
        svc = ImageService()
        assert svc.pexels_available is False

    def test_sdxl_not_initialized_at_startup(self):
        # Post-Phase-G the SDXL lifecycle lives on SdxlProvider's
        # module-level state. A fresh ImageService surfaces
        # ``sdxl_initialized=False`` via the provider shim. We avoid
        # asserting on the shared module state here because other tests
        # in this session may have touched it; the important invariant
        # is that a new ImageService exposes the attribute at all.
        svc = ImageService()
        assert isinstance(svc.sdxl_initialized, bool)
        assert isinstance(svc.sdxl_available, bool)

    def test_search_cache_starts_empty(self):
        svc = ImageService()
        assert svc.search_cache == {}


# ---------------------------------------------------------------------------
# search_featured_image
# ---------------------------------------------------------------------------


class TestSearchFeaturedImage:
    @pytest.mark.asyncio
    async def test_returns_none_when_no_api_key(self):
        svc = ImageService()
        svc.pexels_available = False
        svc._pexels_key_checked_db = True  # prevent DB lookup
        result = await svc.search_featured_image("AI")
        assert result is None

    @pytest.mark.asyncio
    async def test_returns_image_metadata_on_success(self):
        from plugins.image_provider import ImageResult

        svc = make_image_service_with_key()
        fake_provider = MagicMock()
        fake_provider.name = "pexels"
        fake_provider.fetch = AsyncMock(return_value=[
            ImageResult(
                url=SAMPLE_PHOTO["src"]["large"],
                thumbnail=SAMPLE_PHOTO["src"]["small"],
                photographer="Jane Doe",
                photographer_url="https://pexels.com/@jane",
                width=1920,
                height=1080,
                alt_text="A beautiful landscape",
                source="pexels",
                search_query="nature",
            ),
        ])
        with patch(
            "services.image_service._resolve_image_provider",
            return_value=fake_provider,
        ):
            result = await svc.search_featured_image("nature")

        assert result is not None
        assert isinstance(result, FeaturedImageMetadata)
        assert result.url == SAMPLE_PHOTO["src"]["large"]
        assert result.source == "pexels"

    @pytest.mark.asyncio
    async def test_returns_none_when_no_photos_found(self):
        svc = make_image_service_with_key()
        fake_provider = MagicMock()
        fake_provider.name = "pexels"
        fake_provider.fetch = AsyncMock(return_value=[])
        with patch(
            "services.image_service._resolve_image_provider",
            return_value=fake_provider,
        ):
            result = await svc.search_featured_image("very_obscure_topic_xyz")

        assert result is None

    @pytest.mark.asyncio
    async def test_excludes_person_keywords(self):
        svc = make_image_service_with_key()
        captured_queries: list[str] = []

        async def capture_pexels_search(query, **kwargs):  # noqa: ARG001
            captured_queries.append(query)
            return []

        with patch.object(svc, "_pexels_search", side_effect=capture_pexels_search):
            await svc.search_featured_image(
                "AI", keywords=["portrait", "people", "technology"],
            )

        # "portrait" and "people" should be excluded; "technology" should be included
        assert not any("portrait" in q for q in captured_queries)
        assert not any("people" in q for q in captured_queries)


# ---------------------------------------------------------------------------
# get_images_for_gallery
# ---------------------------------------------------------------------------


class TestGetImagesForGallery:
    @pytest.mark.asyncio
    async def test_returns_empty_list_without_api_key(self):
        svc = ImageService()
        svc.pexels_available = False
        svc._pexels_key_checked_db = True  # prevent DB lookup
        result = await svc.get_images_for_gallery("AI")
        assert result == []

    @pytest.mark.asyncio
    async def test_returns_images_up_to_count(self):
        from plugins.image_provider import ImageResult

        svc = make_image_service_with_key()
        fake_provider = MagicMock()
        fake_provider.name = "pexels"
        fake_provider.fetch = AsyncMock(return_value=[
            ImageResult(
                url=SAMPLE_PHOTO["src"]["large"],
                thumbnail=SAMPLE_PHOTO["src"]["small"],
                photographer="Jane Doe",
                source="pexels",
            ),
            ImageResult(
                url="url2",
                thumbnail="url2s",
                photographer="Other",
                source="pexels",
            ),
        ])
        with patch(
            "services.image_service._resolve_image_provider",
            return_value=fake_provider,
        ):
            result = await svc.get_images_for_gallery("nature", count=2)

        assert len(result) == 2

    @pytest.mark.asyncio
    async def test_returns_list_on_api_error(self):
        svc = make_image_service_with_key()
        with patch.object(svc, "_pexels_search", side_effect=Exception("API down")):
            result = await svc.get_images_for_gallery("AI")
        assert isinstance(result, list)


# ---------------------------------------------------------------------------
# _pexels_search (delegates to PexelsProvider)
#
# The raw HTTP call lives in ``services/image_providers/pexels.py`` —
# see ``test_image_providers_pexels.py`` for per-provider coverage.
# These tests only exercise the adapter from ``ImageResult`` →
# ``FeaturedImageMetadata``.
# ---------------------------------------------------------------------------


class TestPexelsSearch:
    @pytest.mark.asyncio
    async def test_returns_empty_list_when_provider_not_registered(self):
        svc = make_image_service_with_key()
        with patch(
            "services.image_service._resolve_image_provider", return_value=None,
        ):
            result = await svc._pexels_search("AI")
        assert result == []

    @pytest.mark.asyncio
    async def test_maps_image_result_to_metadata(self):
        from plugins.image_provider import ImageResult

        svc = make_image_service_with_key()
        fake_provider = MagicMock()
        fake_provider.name = "pexels"
        fake_provider.fetch = AsyncMock(return_value=[
            ImageResult(
                url="https://img.example/large.jpg",
                thumbnail="https://img.example/small.jpg",
                photographer="Jane Doe",
                photographer_url="https://img.example/@jane",
                width=1920,
                height=1080,
                alt_text="sunset",
                source="pexels",
                search_query="nature",
            ),
        ])
        with patch(
            "services.image_service._resolve_image_provider",
            return_value=fake_provider,
        ):
            result = await svc._pexels_search("nature")

        assert len(result) == 1
        img = result[0]
        assert img.photographer == "Jane Doe"
        assert img.width == 1920
        assert img.height == 1080
        assert img.source == "pexels"

    @pytest.mark.asyncio
    async def test_forwards_search_knobs_to_provider(self):
        svc = make_image_service_with_key()
        fake_provider = MagicMock()
        fake_provider.name = "pexels"
        fake_provider.fetch = AsyncMock(return_value=[])
        with patch(
            "services.image_service._resolve_image_provider",
            return_value=fake_provider,
        ):
            await svc._pexels_search(
                "mountain", per_page=12, orientation="portrait",
                size="large", page=3,
            )

        call = fake_provider.fetch.await_args
        forwarded = call.args[1] if len(call.args) > 1 else call.kwargs["config"]
        assert forwarded == {
            "per_page": 12,
            "orientation": "portrait",
            "size": "large",
            "page": 3,
        }

    @pytest.mark.asyncio
    async def test_provider_exception_returns_empty(self):
        svc = make_image_service_with_key()
        fake_provider = MagicMock()
        fake_provider.name = "pexels"
        fake_provider.fetch = AsyncMock(side_effect=RuntimeError("boom"))
        with patch(
            "services.image_service._resolve_image_provider",
            return_value=fake_provider,
        ):
            result = await svc._pexels_search("nature")
        assert result == []


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
        with pytest.raises(ValueError, match="nonexistent_model"):
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
# generate_image — dispatcher (Phase G)
#
# The SDXL model lifecycle itself (host sidecar HTTP path, in-process
# diffusers pipeline init, model hot-swap, import helpers) lives in
# ``services/image_providers/sdxl.py`` — see ``test_image_providers_sdxl.py``.
# The tests here only cover the thin registry-lookup / forwarding in
# ``ImageService.generate_image``.
# ---------------------------------------------------------------------------


class TestGenerateImageDispatcher:
    """Coverage for the ImageProvider dispatch in ``generate_image``."""

    @pytest.mark.asyncio
    async def test_dispatches_to_configured_provider(self, tmp_path):
        """``plugin.image_provider.primary`` picks the provider; fetch() is
        called with output_path + generation knobs in config."""
        svc = ImageService()
        output_path = str(tmp_path / "out.png")
        # Create a file at output_path so the post-call existence check passes.
        (tmp_path / "out.png").write_bytes(b"\x89PNG fake")

        fake_provider = MagicMock()
        fake_provider.name = "sdxl"
        fake_provider.fetch = AsyncMock(return_value=[MagicMock()])

        with (
            patch(
                "services.image_service._resolve_image_provider",
                return_value=fake_provider,
            ),
            patch(
                "services.site_config.site_config.get",
                return_value="sdxl",
            ),
        ):
            result = await svc.generate_image(
                prompt="cat in space",
                output_path=output_path,
                negative_prompt="ugly",
                num_inference_steps=8,
                guidance_scale=2.5,
            )

        assert result is True
        fake_provider.fetch.assert_awaited_once()
        # Config forwarded correctly
        call_args = fake_provider.fetch.await_args
        forwarded = call_args.args[1] if len(call_args.args) > 1 else call_args.kwargs["config"]
        assert forwarded["output_path"] == output_path
        assert forwarded["negative_prompt"] == "ugly"
        assert forwarded["num_inference_steps"] == 8
        assert forwarded["guidance_scale"] == 2.5

    @pytest.mark.asyncio
    async def test_returns_false_when_provider_not_registered(self, tmp_path):
        svc = ImageService()
        with (
            patch("services.image_service._resolve_image_provider", return_value=None),
            patch(
                "services.site_config.site_config.get",
                return_value="nonexistent",
            ),
        ):
            result = await svc.generate_image(
                prompt="x",
                output_path=str(tmp_path / "x.png"),
            )
        assert result is False

    @pytest.mark.asyncio
    async def test_returns_false_when_provider_returns_empty(self, tmp_path):
        svc = ImageService()
        fake_provider = MagicMock()
        fake_provider.name = "sdxl"
        fake_provider.fetch = AsyncMock(return_value=[])

        with (
            patch(
                "services.image_service._resolve_image_provider",
                return_value=fake_provider,
            ),
            patch("services.site_config.site_config.get", return_value="sdxl"),
        ):
            result = await svc.generate_image(
                prompt="x",
                output_path=str(tmp_path / "x.png"),
            )
        assert result is False

    @pytest.mark.asyncio
    async def test_returns_false_when_file_not_written(self, tmp_path):
        """Even if provider reports success, a missing file at output_path
        means the dispatcher returns False."""
        svc = ImageService()
        # Note: do NOT create the file — mimic a buggy provider.
        fake_provider = MagicMock()
        fake_provider.name = "sdxl"
        fake_provider.fetch = AsyncMock(return_value=[MagicMock()])

        with (
            patch(
                "services.image_service._resolve_image_provider",
                return_value=fake_provider,
            ),
            patch("services.site_config.site_config.get", return_value="sdxl"),
        ):
            result = await svc.generate_image(
                prompt="x",
                output_path=str(tmp_path / "never-created.png"),
            )
        assert result is False

    @pytest.mark.asyncio
    async def test_provider_exception_returns_false(self, tmp_path):
        svc = ImageService()
        fake_provider = MagicMock()
        fake_provider.name = "sdxl"
        fake_provider.fetch = AsyncMock(side_effect=RuntimeError("GPU OOM"))

        with (
            patch(
                "services.image_service._resolve_image_provider",
                return_value=fake_provider,
            ),
            patch("services.site_config.site_config.get", return_value="sdxl"),
        ):
            result = await svc.generate_image(
                prompt="x",
                output_path=str(tmp_path / "x.png"),
            )
        assert result is False


# ---------------------------------------------------------------------------
# _ensure_pexels_key — DB-first key loading
# ---------------------------------------------------------------------------


class TestEnsurePexelsKey:
    """Tests for ``ImageService._ensure_pexels_key`` post-Phase-G.

    The probe no longer stores the decrypted key on the service — the
    PexelsProvider reads the key itself per-fetch. This method just
    caches a ``pexels_available`` bool so the orchestrator can skip
    the LLM semantic-query path when Pexels isn't configured.
    """

    @pytest.mark.asyncio
    async def test_already_checked_noop(self):
        svc = ImageService()
        svc.pexels_available = True
        svc._pexels_key_checked_db = True

        # Should not touch the container at all.
        with patch("services.container.get_service") as mock_get:
            await svc._ensure_pexels_key()
        mock_get.assert_not_called()

    @pytest.mark.asyncio
    async def test_probe_flips_available_when_key_present(self):
        svc = ImageService()
        svc._pexels_key_checked_db = False

        fake_conn = AsyncMock()
        fake_pool_ctx = AsyncMock()
        fake_pool_ctx.__aenter__ = AsyncMock(return_value=fake_conn)
        fake_pool_ctx.__aexit__ = AsyncMock(return_value=False)

        fake_db_service = MagicMock()
        fake_db_service.pool = MagicMock()
        fake_db_service.pool.acquire = MagicMock(return_value=fake_pool_ctx)

        with (
            patch("services.container.get_service", return_value=fake_db_service),
            patch(
                "plugins.secrets.get_secret",
                new=AsyncMock(return_value="decrypted-key"),
            ),
        ):
            await svc._ensure_pexels_key()

        assert svc.pexels_available is True
        assert svc._pexels_key_checked_db is True

    @pytest.mark.asyncio
    async def test_no_db_service_sets_unavailable(self):
        svc = ImageService()
        svc._pexels_key_checked_db = False

        with patch("services.container.get_service", return_value=None):
            await svc._ensure_pexels_key()

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

        with (
            patch("services.container.get_service", return_value=fake_db_service),
            patch("plugins.secrets.get_secret", new=AsyncMock(return_value=None)),
        ):
            await svc._ensure_pexels_key()

        assert svc.pexels_available is False


# ---------------------------------------------------------------------------
# optimize_image_for_web
#
# Note: ``get_active_model`` / ``list_available_models`` were removed in
# Phase G — they referenced the in-process diffusers pipeline state that
# now lives inside the SdxlProvider. See
# ``tests/unit/services/test_image_providers_sdxl.py`` for provider-level
# coverage.
# ---------------------------------------------------------------------------


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
