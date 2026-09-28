"""Shared image model registry — model names, their configs, the default resolver.

``ImageModel``, ``ImageModelConfig`` / ``IMAGE_MODEL_REGISTRY`` and the
``image_model`` default resolver. Extracted from ``services/image_service.py``
in Phase G (GH#71); image_service re-exports these names so existing callers
and tests keep importing them from there.

Model list:

- ``SDXL_BASE`` — stabilityai/stable-diffusion-xl-base-1.0 (30 steps, ~6GB)
- ``SDXL_LIGHTNING`` — SDXL base + ByteDance Lightning LoRA (4 steps, ~6GB)
- ``FLUX_SCHNELL`` — black-forest-labs/FLUX.1-schnell (4 steps, ~12GB)
- ``Z_IMAGE_TURBO`` — Tongyi-MAI/Z-Image-Turbo (9 steps, ~13GB)

Every render happens in the image-gen HTTP server
(``scripts/image-gen-server.py``, its own CUDA container), which keeps its
own registry and picks its model from ``app_settings.image_generation_model``.
The ``pipeline_class`` / LoRA / scheduler / dtype fields below describe the
worker's retired in-process diffusers path. Nothing in the worker loads
them, and this module imports no torch, diffusers or xformers: an import
probe here used to pull CPU torch into every process that imported
image_service.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Any

from poindexter.services.logger_config import get_logger

logger = get_logger(__name__)

__all__ = [
    "IMAGE_MODEL_REGISTRY",
    "ImageModel",
    "ImageModelConfig",
    "get_default_image_model",
]


# ---------------------------------------------------------------------------
# Model registry
# ---------------------------------------------------------------------------


class ImageModel(str, Enum):
    """Available image generation models."""

    SDXL_BASE = "sdxl_base"
    SDXL_LIGHTNING = "sdxl_lightning"
    FLUX_SCHNELL = "flux_schnell"
    Z_IMAGE_TURBO = "z_image_turbo"


@dataclass(frozen=True)
class ImageModelConfig:
    """Configuration for an image generation model."""

    model_id: str
    display_name: str
    default_steps: int
    default_guidance_scale: float
    pipeline_class: str  # dotted import path within diffusers
    lora_repo: str | None = None
    lora_weight_name: str | None = None
    scheduler_override: str | None = None  # e.g. "EulerDiscreteScheduler"
    scheduler_kwargs: dict[str, Any] | None = None
    torch_dtype_str: str = "float16"  # "float16" or "bfloat16"
    vram_gb: float = 6.0
    notes: str = ""


IMAGE_MODEL_REGISTRY: dict[ImageModel, ImageModelConfig] = {
    ImageModel.SDXL_BASE: ImageModelConfig(
        model_id="stabilityai/stable-diffusion-xl-base-1.0",
        display_name="Stable Diffusion XL Base",
        default_steps=30,
        default_guidance_scale=7.5,
        pipeline_class="diffusers.StableDiffusionXLPipeline",
        vram_gb=6.5,
        notes="Original Stable Diffusion XL, high quality at 30-50 steps",
    ),
    ImageModel.SDXL_LIGHTNING: ImageModelConfig(
        model_id="stabilityai/stable-diffusion-xl-base-1.0",
        display_name="Stable Diffusion XL Lightning",
        default_steps=4,
        default_guidance_scale=0.0,
        pipeline_class="diffusers.StableDiffusionXLPipeline",
        lora_repo="ByteDance/SDXL-Lightning",
        lora_weight_name="sdxl_lightning_4step_lora.safetensors",
        scheduler_override="EulerDiscreteScheduler",
        scheduler_kwargs={"timestep_spacing": "trailing"},
        vram_gb=6.5,
        notes="4-step distilled LoRA — 10x faster, great quality",
    ),
    ImageModel.FLUX_SCHNELL: ImageModelConfig(
        model_id="black-forest-labs/FLUX.1-schnell",
        display_name="Flux.1 Schnell",
        default_steps=4,
        default_guidance_scale=0.0,
        pipeline_class="diffusers.FluxPipeline",
        torch_dtype_str="bfloat16",
        vram_gb=12.0,
        notes="Best quality, needs ~12GB VRAM",
    ),
    ImageModel.Z_IMAGE_TURBO: ImageModelConfig(
        model_id="Tongyi-MAI/Z-Image-Turbo",
        display_name="Z-Image-Turbo",
        default_steps=9,
        default_guidance_scale=0.0,
        pipeline_class="diffusers.ZImagePipeline",
        torch_dtype_str="bfloat16",
        vram_gb=13.0,
        notes=(
            "Apache-2.0 6B guidance-distilled turbo (9 steps / CFG 0 / bf16, "
            "no negative prompt). 2026-06-19 bake-off default. Mirrors the image-gen "
            "HTTP server registry (scripts/image-gen-server.py), the live render path."
        ),
    ),
}


# The fallback when app_settings has no usable ``image_model``. Kept as ONE
# constant because this function previously spelled ``sdxl_lightning`` four
# times over (None-path, inline get() default, warning copy, ValueError path) —
# and #2386's bake-off moved the seeded default to z_image_turbo without
# catching any of them. Pinned to settings_defaults.DEFAULTS['image_model'] by
# tests/unit/services/test_inline_defaults_match_seed.py.
_DEFAULT_IMAGE_MODEL = ImageModel.Z_IMAGE_TURBO


def get_default_image_model(site_config: Any = None) -> ImageModel:
    """Get the default image model from site_config, or the built-in fallback.

    Phase H step 5 (GH#95): when ``site_config`` is None (dispatcher hasn't
    seeded it yet), returns ``_DEFAULT_IMAGE_MODEL``. Callers with an explicit
    instance (e.g. DI from app.state) can pass it in to honor the app_settings
    override.
    """
    if site_config is None:
        return _DEFAULT_IMAGE_MODEL
    model_name = site_config.get("image_model", _DEFAULT_IMAGE_MODEL.value)
    try:
        return ImageModel(model_name)
    except ValueError:
        logger.warning(
            "Unknown IMAGE_MODEL '%s', falling back to %s",
            model_name,
            _DEFAULT_IMAGE_MODEL.value,
        )
        return _DEFAULT_IMAGE_MODEL
