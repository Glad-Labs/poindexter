"""SdxlProvider — self-contained text-to-image generation.

Phase G inversion (GitHub #71). Previously this provider wrapped
``services.image_service.ImageService.generate_image``; that left the
diffusers model lifecycle, the host-sidecar HTTP call, and the upload
paths tangled inside the legacy god-class. The provider now owns all
three. ``ImageService.generate_image`` is a thin dispatcher that reads
``plugin.image_provider.primary`` from site_config and forwards here.

Two generation strategies, tried in order:

1. **Host SDXL sidecar** — HTTP POST to ``sdxl_server_url``
   (default ``http://host.docker.internal:9836``). Fast path for the
   sidecar Matt runs on his workstation.
2. **In-process diffusers** — torch + diffusers fallback used when the
   host sidecar is unreachable. Slower; loads a pipeline on first use
   and keeps it in module-level state for the worker lifetime.

Config (``plugin.image_provider.sdxl`` in app_settings — also accepts
these keys via the dispatcher's per-call forwarding):

- ``enabled`` (default true)
- ``negative_prompt`` (default read from ``app_settings.image_negative_prompt``)
- ``output_path`` — where to write the PNG. When absent, a tempfile is
  used. The dispatcher always passes ``output_path`` so
  ``ImageService.generate_image(output_path=...)`` callers get the file
  at the path they asked for.
- ``num_inference_steps`` — overrides the active model's default
- ``guidance_scale`` — overrides
- ``task_id`` — WebSocket progress stream identifier
- ``model`` — ImageModel enum value ("sdxl_base", "sdxl_lightning",
  "flux_schnell"). Defaults to ``site_config.image_model``.
- ``upload_to`` — ``""``, ``"cloudinary"``, or ``"r2"``. Controls
  post-generation upload; the ImageResult.url reflects the target.

Kind: ``"generate"``.
"""

from __future__ import annotations

import asyncio
import importlib
import logging
import os
import tempfile
import time
from typing import Any

import httpx

from plugins.image_provider import ImageResult
from services.image_providers._sdxl_models import (
    DIFFUSERS_AVAILABLE,
    IMAGE_MODEL_REGISTRY,
    TORCH_AVAILABLE,
    XFORMERS_AVAILABLE,
    ImageModel,
    get_default_image_model,
    torch,
)

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Module-level pipeline state. Loading an SDXL pipeline takes 20s (SDXL
# Lightning) to ~1 minute (Flux) of cold start, so the pipeline persists
# across ``fetch()`` calls for the worker process lifetime. The host-
# sidecar path doesn't touch this state — only the in-process diffusers
# fallback does. ``services.gpu_scheduler._unload_sdxl`` coordinates
# freeing VRAM via the sidecar's ``/unload`` HTTP endpoint; we don't
# coordinate from this module directly.
# ---------------------------------------------------------------------------


class _SdxlPipelineState:
    """Shared in-process pipeline state. Exposed so ImageService can
    surface ``sdxl_available`` / ``sdxl_initialized`` flags to callers
    that branch on whether in-process generation is ready."""

    def __init__(self) -> None:
        self.pipe: Any = None
        self.active_model: ImageModel | None = None
        self.use_device: str = "cpu"
        self.initialized: bool = False  # Have we tried to init diffusers yet?
        self.available: bool = False    # Did diffusers init succeed?


_state = _SdxlPipelineState()


def _write_image_bytes(path: str, content: bytes) -> None:
    """Sync helper for ``asyncio.to_thread`` — writes sidecar response
    to disk. SDXL images are 1–5 MB; a blocking ``open()`` at that size
    would stall the event loop under concurrent load (ASYNC230).
    """
    with open(path, "wb") as f:
        f.write(content)


# ---------------------------------------------------------------------------
# Provider surface
# ---------------------------------------------------------------------------


class SdxlProvider:
    """SDXL text-to-image via host sidecar or in-process diffusers."""

    name = "sdxl"
    kind = "generate"

    async def fetch(
        self,
        query_or_prompt: str,
        config: dict[str, Any],
    ) -> list[ImageResult]:
        prompt = (query_or_prompt or "").strip()
        if not prompt:
            return []

        negative = str(config.get("negative_prompt", "") or "")
        if not negative:
            try:
                from services.site_config import site_config
                negative = site_config.get("image_negative_prompt", "") or ""
            except Exception:
                negative = ""

        output_path = str(config.get("output_path", "") or "")
        cleanup_on_failure = False
        if not output_path:
            with tempfile.NamedTemporaryFile(suffix=".png", delete=False) as tmp:
                output_path = tmp.name
            cleanup_on_failure = True

        target_model: ImageModel | None = None
        raw_model = config.get("model")
        if raw_model:
            try:
                target_model = ImageModel(str(raw_model))
            except ValueError:
                target_model = None

        steps = config.get("num_inference_steps")
        guidance = config.get("guidance_scale")
        task_id = config.get("task_id")

        success = await _generate_to_path(
            prompt=prompt,
            negative=negative,
            output_path=output_path,
            steps=int(steps) if steps is not None else None,
            guidance=float(guidance) if guidance is not None else None,
            task_id=str(task_id) if task_id is not None else None,
            target_model=target_model,
        )

        if not success or not os.path.exists(output_path):
            if cleanup_on_failure and os.path.exists(output_path):
                try:
                    os.remove(output_path)
                except OSError:
                    pass
            return []

        url = f"file://{output_path}"
        upload_target = str(config.get("upload_to", "") or "")
        if upload_target == "cloudinary":
            try:
                url = await _upload_to_cloudinary(output_path, prompt)
            except Exception as e:
                logger.warning(
                    "[SdxlProvider] Cloudinary upload failed (serving file:// URL): %s",
                    e,
                )
        elif upload_target == "r2":
            try:
                url = await _upload_to_r2(output_path, prompt)
            except Exception as e:
                logger.warning(
                    "[SdxlProvider] R2 upload failed (serving file:// URL): %s", e,
                )

        return [
            ImageResult(
                url=url,
                thumbnail=url,
                photographer="Glad Labs SDXL",
                photographer_url="",
                alt_text=prompt[:200],
                source=self.name,
                search_query=prompt,
                metadata={
                    "local_path": output_path,
                    "negative_prompt": negative,
                    "upload_target": upload_target or "none",
                },
            ),
        ]


# ---------------------------------------------------------------------------
# Generation strategies — host sidecar + in-process diffusers
# ---------------------------------------------------------------------------


async def _generate_to_path(
    *,
    prompt: str,
    negative: str,
    output_path: str,
    steps: int | None,
    guidance: float | None,
    task_id: str | None,
    target_model: ImageModel | None,
) -> bool:
    """Run the sidecar + diffusers strategies in order. Returns True
    when a file has been written to ``output_path``.
    """
    if await _try_host_sidecar(
        prompt=prompt,
        negative=negative,
        output_path=output_path,
        steps=steps,
        guidance=guidance,
    ):
        return True
    return await _try_in_process_diffusers(
        prompt=prompt,
        negative=negative,
        output_path=output_path,
        steps=steps,
        guidance=guidance,
        task_id=task_id,
        target_model=target_model,
    )


async def _try_host_sidecar(
    *,
    prompt: str,
    negative: str,
    output_path: str,
    steps: int | None,
    guidance: float | None,
) -> bool:
    """POST the prompt to the host SDXL sidecar. Return True on 200 +
    ``image/*`` content-type with bytes written to ``output_path``."""
    try:
        from services.site_config import site_config
        server_url = site_config.get(
            "sdxl_server_url", "http://host.docker.internal:9836",
        )
    except Exception:
        server_url = "http://host.docker.internal:9836"

    try:
        # 60s per-call cap — Lightning renders in ~1-2s, cold-load
        # takes ~10s, allow headroom for network + retries.
        async with httpx.AsyncClient(
            timeout=httpx.Timeout(60.0, connect=5.0),
        ) as client:
            resp = await client.post(
                f"{server_url}/generate",
                json={
                    "prompt": prompt,
                    "negative_prompt": negative,
                    "steps": steps or 4,
                    "guidance_scale": guidance if guidance is not None else 1.0,
                },
                timeout=60,
            )
            ct = resp.headers.get("content-type", "")
            if resp.status_code == 200 and ct.startswith("image/"):
                await asyncio.to_thread(
                    _write_image_bytes, output_path, resp.content,
                )
                elapsed = resp.headers.get("X-Elapsed-Seconds", "?")
                logger.info(
                    "SDXL image generated via host server in %ss: %s",
                    elapsed, output_path,
                )
                return True
            logger.warning(
                "SDXL server returned %s (content-type=%r): %s",
                resp.status_code, ct, resp.text[:200],
            )
    except Exception as e:
        logger.info(
            "SDXL host server unavailable (%s), trying local diffusers...", e,
        )
    return False


async def _try_in_process_diffusers(
    *,
    prompt: str,
    negative: str,
    output_path: str,
    steps: int | None,
    guidance: float | None,
    task_id: str | None,
    target_model: ImageModel | None,
) -> bool:
    """Initialize (if needed) + run the in-process diffusers pipeline."""
    if not _state.initialized or (
        target_model is not None and target_model != _state.active_model
    ):
        model = target_model or get_default_image_model()
        logger.info(
            "First in-process diffusers request — initializing %s...", model.value,
        )
        _initialize_model(model)
        _state.initialized = True

    if not _state.available or _state.pipe is None:
        logger.warning(
            "In-process diffusers pipeline not available — generation skipped",
        )
        return False

    active = _state.active_model
    if active is None:
        return False
    cfg = IMAGE_MODEL_REGISTRY[active]
    effective_steps = steps if steps is not None else cfg.default_steps
    effective_guidance = (
        guidance if guidance is not None else cfg.default_guidance_scale
    )

    try:
        logger.info("Generating image for prompt: '%s'", prompt)
        logger.info(
            "   Model: %s, steps=%s, guidance=%s, device=%s",
            cfg.display_name, effective_steps, effective_guidance,
            _state.use_device.upper(),
        )
        loop = asyncio.get_event_loop()
        await loop.run_in_executor(
            None,
            _generate_image_sync,
            prompt,
            output_path,
            negative or None,
            effective_steps,
            effective_guidance,
            task_id,
        )
        logger.info("Image saved to %s", output_path)
        if task_id:
            from services.progress_broadcaster import broadcast_progress
            from services.progress_service import get_progress_service
            progress_service = get_progress_service()
            progress_service.mark_complete(task_id, "Image generation complete")
            progress = progress_service.get_progress(task_id)
            await broadcast_progress(task_id, progress)
        return True
    except Exception as e:
        logger.error("Error generating image: %s", e, exc_info=True)
        if task_id:
            from services.progress_broadcaster import broadcast_progress
            from services.progress_service import get_progress_service
            progress_service = get_progress_service()
            progress_service.mark_failed(task_id, str(e))
            progress = progress_service.get_progress(task_id)
            if progress is not None:
                await broadcast_progress(task_id, progress)
        return False


# ---------------------------------------------------------------------------
# In-process diffusers pipeline lifecycle. Was ``ImageService._*_model``
# + ``_generate_image_sync``. Module-level functions operating on the
# shared ``_state`` — simpler than a class, and keeps each strategy's
# state localized to the provider that owns it.
# ---------------------------------------------------------------------------


def _initialize_model(model: ImageModel | None = None) -> None:
    """Initialize or switch the in-process diffusers pipeline.

    Supports lazy loading, hot-swapping between models, and automatic
    cleanup of previously loaded models to free VRAM.
    """
    if model is None:
        model = get_default_image_model()

    if _state.active_model == model and _state.pipe is not None:
        logger.debug("Model %s already loaded, skipping init", model.value)
        return

    if not DIFFUSERS_AVAILABLE:
        logger.warning(
            "Diffusers library not installed - image generation will be unavailable",
        )
        _state.available = False
        return

    if not TORCH_AVAILABLE:
        logger.warning(
            "PyTorch not installed - image generation will be unavailable",
        )
        _state.available = False
        return

    if _state.pipe is not None:
        logger.info(
            "Switching model: %s -> %s",
            _state.active_model.value if _state.active_model else "none",
            model.value,
        )
        _unload_model()

    cfg = IMAGE_MODEL_REGISTRY[model]

    try:
        use_device = "cpu"
        torch_dtype = torch.float32 if TORCH_AVAILABLE else None

        if TORCH_AVAILABLE and torch.cuda.is_available():
            try:
                capability = torch.cuda.get_device_capability(0)
                device_name = torch.cuda.get_device_name(0)
                current_cap = capability[0] * 10 + capability[1]
                supported_caps = [50, 60, 61, 70, 75, 80, 86, 90, 120]

                logger.info(
                    "GPU: %s, Capability: sm_%s%s",
                    device_name, capability[0], capability[1],
                )

                if current_cap in supported_caps:
                    use_device = "cuda"
                    gpu_memory = (
                        torch.cuda.get_device_properties(0).total_memory / (1024**3)
                    )
                    logger.info(
                        "GPU Memory: %.1fGB - Using CUDA acceleration", gpu_memory,
                    )
                else:
                    logger.warning(
                        "GPU capability sm_%s%s not officially supported. "
                        "Falling back to CPU mode.",
                        capability[0], capability[1],
                    )
            except Exception as e:
                logger.warning(
                    "Could not verify GPU capability: %s. Using CPU mode.",
                    e, exc_info=True,
                )
        else:
            logger.warning("CUDA not available - using CPU mode (slower)")

        if use_device == "cpu":
            torch_dtype = torch.float32
            logger.info("CPU mode: using fp32 (full precision)")
        elif cfg.torch_dtype_str == "bfloat16":
            torch_dtype = torch.bfloat16
            logger.info("Using bfloat16 precision")
        else:
            torch_dtype = torch.float16
            logger.info("Using fp16 (half precision) for memory efficiency")

        pipeline_cls = _import_pipeline_class(cfg.pipeline_class)

        logger.info(
            "Loading %s (%s) on %s...",
            cfg.display_name, cfg.model_id, use_device,
        )
        load_kwargs: dict[str, Any] = {
            "torch_dtype": torch_dtype,
            "use_safetensors": True,
        }
        if torch_dtype == torch.float16:
            load_kwargs["variant"] = "fp16"

        pipe = pipeline_cls.from_pretrained(cfg.model_id, **load_kwargs).to(
            use_device,
        )

        if cfg.lora_repo:
            logger.info("Loading LoRA weights from %s...", cfg.lora_repo)
            pipe.load_lora_weights(cfg.lora_repo, weight_name=cfg.lora_weight_name)
            pipe.fuse_lora()
            logger.info("LoRA weights fused successfully")

        if cfg.scheduler_override:
            logger.info("Applying scheduler override: %s", cfg.scheduler_override)
            from diffusers import EulerDiscreteScheduler  # noqa: PLC0415

            sched_kwargs = cfg.scheduler_kwargs or {}
            pipe.scheduler = EulerDiscreteScheduler.from_config(
                pipe.scheduler.config, **sched_kwargs,
            )

        _apply_model_optimizations(pipe, use_device)

        _state.pipe = pipe
        _state.active_model = model
        _state.use_device = use_device
        _state.available = True

        logger.info("%s loaded successfully", cfg.display_name)
        logger.info("   Device: %s", use_device.upper())
        logger.info(
            "   Default steps: %s, guidance: %s",
            cfg.default_steps, cfg.default_guidance_scale,
        )
        logger.info(
            "   Optimizations: %s",
            "ENABLED (xformers)" if XFORMERS_AVAILABLE else "BASIC (no xformers)",
        )

    except Exception as e:
        logger.error(
            "Failed to load %s: %s", cfg.display_name, e, exc_info=True,
        )
        _state.available = False


def _unload_model() -> None:
    """Unload the current pipeline and free VRAM/RAM."""
    if _state.pipe is not None:
        model_name = (
            _state.active_model.value if _state.active_model else "unknown"
        )
        logger.info("Unloading model: %s", model_name)
        del _state.pipe

    _state.pipe = None
    _state.active_model = None
    _state.available = False

    if TORCH_AVAILABLE and torch.cuda.is_available():
        torch.cuda.empty_cache()
        logger.debug("CUDA cache cleared")


def _import_pipeline_class(dotted_path: str):
    """Dynamically import a diffusers pipeline class from a dotted path."""
    parts = dotted_path.rsplit(".", 1)
    if len(parts) != 2:
        raise ImportError(f"Invalid pipeline class path: {dotted_path}")
    module_path, class_name = parts
    module = importlib.import_module(module_path)
    return getattr(module, class_name)


def _apply_model_optimizations(pipe: Any, device: str) -> None:
    """Apply performance optimizations to the pipeline.

    Attention slicing + optional xformers + Flash Attention v2 + CPU
    offload where applicable. Works on both CPU and GPU; best-effort
    per optimization — failures are logged and ignored so a missing
    package doesn't break the whole pipeline.
    """
    try:
        pipe.enable_attention_slicing()
        logger.info("   Attention slicing enabled")

        if XFORMERS_AVAILABLE:
            try:
                pipe.enable_xformers_memory_efficient_attention()
                logger.info(
                    "   xformers memory-efficient attention enabled (2-4x faster)",
                )
            except Exception as e:
                logger.warning("   Could not enable xformers: %s", e, exc_info=True)

        try:
            if hasattr(pipe.unet, "enable_flash_attn"):
                pipe.unet.enable_flash_attn(use_flash_attention_v2=True)
                logger.info("   Flash Attention v2 enabled (30-50% faster)")
        except Exception as e:
            logger.debug("   Flash Attention v2 not available: %s", e)

        if device == "cuda":
            try:
                pipe.enable_sequential_cpu_offload()
                logger.info(
                    "   Sequential CPU offloading enabled (GPU memory saver)",
                )
            except Exception as e:
                logger.debug("   Sequential CPU offload not available: %s", e)

            try:
                gpu_mem = (
                    torch.cuda.get_device_properties(0).total_memory / (1024**3)
                )
                if gpu_mem < 20:
                    pipe.enable_model_cpu_offload()
                    logger.info(
                        "   Model CPU offload enabled (constrained GPU memory)",
                    )
            except Exception as e:
                logger.debug("   Model CPU offload not available: %s", e)

    except Exception as e:
        logger.warning("Error applying optimizations: %s", e, exc_info=True)


def _generate_image_sync(
    prompt: str,
    output_path: str,
    negative_prompt: str | None,
    num_inference_steps: int,
    guidance_scale: float,
    task_id: str | None,
) -> None:
    """Run the sync pipeline call inside a thread-pool worker.

    Emits progress updates via ``services.progress_service`` when
    ``task_id`` is set so the WebSocket stream can show per-step
    generation progress to the caller.
    """
    if not _state.pipe:
        raise RuntimeError("Image generation pipeline not initialized")

    negative_prompt = negative_prompt or ""
    start_time = time.time()

    progress_service = None
    if task_id:
        from services.progress_service import get_progress_service

        progress_service = get_progress_service()
        progress_service.create_progress(task_id, num_inference_steps)

    def progress_callback(step: int, _timestep: Any, _latents: Any) -> None:
        if progress_service and task_id:
            elapsed = time.time() - start_time
            progress_service.update_progress(
                task_id,
                step + 1,
                stage="generation",
                elapsed_time=elapsed,
                message=f"Generating: step {step + 1}/{num_inference_steps}",
            )

    model_name = (
        _state.active_model.value if _state.active_model else "unknown"
    )
    logger.info(
        "   Generating with %s (%s steps)...", model_name, num_inference_steps,
    )

    if progress_service and task_id:
        progress_service.update_progress(
            task_id, 0, stage="generation",
            message="Starting image generation...",
        )

    result = _state.pipe(
        prompt=prompt,
        negative_prompt=negative_prompt,
        num_inference_steps=num_inference_steps,
        guidance_scale=guidance_scale,
        output_type="pil",
        callback=progress_callback if task_id else None,
        callback_steps=1 if task_id else None,
    )

    image = result.images[0]
    logger.info("   Generation complete, saving image...")

    try:
        image.save(output_path)
        elapsed = time.time() - start_time
        logger.info("   Image saved to %s (%.1fs)", output_path, elapsed)
    except Exception as save_error:
        logger.error("   Save failed: %s", save_error, exc_info=True)
        raise


# ---------------------------------------------------------------------------
# Upload helpers — unchanged from the previous wrapper provider
# ---------------------------------------------------------------------------


async def _upload_to_cloudinary(path: str, prompt: str) -> str:
    """Upload a generated PNG to Cloudinary and return the secure URL."""
    import cloudinary
    import cloudinary.uploader

    from services.site_config import site_config

    cloudinary.config(
        cloud_name=site_config.get("cloudinary_cloud_name"),
        api_key=site_config.get("cloudinary_api_key"),
        api_secret=site_config.get("cloudinary_api_secret"),
    )

    def _upload() -> dict:
        return cloudinary.uploader.upload(
            path,
            folder="generated/",
            resource_type="image",
            tags=["sdxl", "provider"],
            context={"alt": prompt[:200]},
        )

    loop = asyncio.get_event_loop()
    result = await loop.run_in_executor(None, _upload)
    url = result.get("secure_url", "")
    if not url:
        raise RuntimeError("Cloudinary returned empty secure_url")
    return str(url)


async def _upload_to_r2(path: str, prompt: str) -> str:
    """Upload a generated PNG to R2 via the shared r2_upload_service."""
    from services.r2_upload_service import upload_to_r2
    key = f"sdxl/{os.path.basename(path)}"
    url = await upload_to_r2(path, key, "image/png")
    if not url:
        raise RuntimeError("r2_upload_service returned empty URL")
    logger.debug("[SdxlProvider] uploaded %s for prompt %r", key, prompt[:40])
    return str(url)
