"""Shared SDXL model registry + torch availability probes.

Extracted from ``services/image_service.py`` in Phase G (GH#71) so the
``SdxlProvider`` can own the model lifecycle without image_service acting
as a middleman. The registry + enum + default-resolver are imported from
both sides during the cutover; image_service re-exports them for
backward compatibility so existing callers and test patches keep working.

Model list:

- ``SDXL_BASE`` — stabilityai/stable-diffusion-xl-base-1.0 (30 steps, ~6GB)
- ``SDXL_LIGHTNING`` — SDXL base + ByteDance Lightning LoRA (4 steps, ~6GB)
- ``FLUX_SCHNELL`` — black-forest-labs/FLUX.1-schnell (4 steps, ~12GB)

All options are local GPU / CPU fallback — $0/month inference.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from importlib.util import find_spec
from typing import Any

from services.logger_config import get_logger

logger = get_logger(__name__)


# ---------------------------------------------------------------------------
# Optional dependency probes. Kept as module-level constants so both
# image_service (legacy callers, tests) and sdxl provider can branch on
# availability without re-running the imports.
#
# ``torch`` is bound at module scope so test patches like
# ``patch("services.image_service.torch", mock_torch)`` keep working
# after the Phase G cutover — image_service.py re-exports it. The
# diffusers pipeline classes are resolved dynamically by
# ``ImageModelConfig.pipeline_class`` via ``importlib.import_module`` so
# we don't need to import them at module scope here.
# ---------------------------------------------------------------------------

try:
    import torch

    TORCH_AVAILABLE = True
except ImportError:
    torch = None  # type: ignore[assignment]
    TORCH_AVAILABLE = False

# Explicit re-export so static analyzers don't flag ``torch`` as unused.
# image_service.py imports ``torch`` from this module to preserve the
# ``patch("services.image_service.torch", mock)`` test hook.
__all__ = [
    "DIFFUSERS_AVAILABLE",
    "IMAGE_MODEL_REGISTRY",
    "ImageModel",
    "ImageModelConfig",
    "TORCH_AVAILABLE",
    "XFORMERS_AVAILABLE",
    "get_default_image_model",
    "torch",
]

DIFFUSERS_AVAILABLE = find_spec("diffusers") is not None
if not DIFFUSERS_AVAILABLE:
    logger.warning("Diffusers library not available")

XFORMERS_AVAILABLE = find_spec("xformers") is not None


# ---------------------------------------------------------------------------
# Model registry
# ---------------------------------------------------------------------------


class ImageModel(str, Enum):
    """Available image generation models."""

    SDXL_BASE = "sdxl_base"
    SDXL_LIGHTNING = "sdxl_lightning"
    FLUX_SCHNELL = "flux_schnell"


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
        display_name="SDXL Base",
        default_steps=30,
        default_guidance_scale=7.5,
        pipeline_class="diffusers.StableDiffusionXLPipeline",
        vram_gb=6.5,
        notes="Original SDXL, high quality at 30-50 steps",
    ),
    ImageModel.SDXL_LIGHTNING: ImageModelConfig(
        model_id="stabilityai/stable-diffusion-xl-base-1.0",
        display_name="SDXL Lightning",
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
}


def get_default_image_model(site_config: Any = None) -> ImageModel:
    """Get the default image model from site_config or fallback.

    Phase H step 4.5 (GH#95): site_config is an optional param so callers
    with an explicit instance (e.g. DI from app.state) can pass it in.
    Falls back to the module singleton for callers that haven't migrated
    — removed in Phase H step 5.
    """
    if site_config is None:
        from services.site_config import site_config
    model_name = site_config.get("image_model", "sdxl_lightning")
    try:
        return ImageModel(model_name)
    except ValueError:
        logger.warning(
            "Unknown IMAGE_MODEL '%s', falling back to sdxl_lightning", model_name,
        )
        return ImageModel.SDXL_LIGHTNING
