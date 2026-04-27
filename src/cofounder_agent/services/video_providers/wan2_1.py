"""Wan21Provider — text-to-video via Wan-AI/Wan2.1-T2V-1.3B inference server.

The first concrete :class:`VideoProvider <plugins.video_provider.VideoProvider>`
implementation. Tracks GitHub #124 — adds a true T2V engine alongside
the legacy Ken Burns slideshow pipeline so a single
``app_settings.video_engine`` flip can swap between them.

License: **Apache-2.0** (`Wan-AI/Wan2.1-T2V-1.3B`). The 14B variant
(also Apache-2.0) ships in a follow-up ticket once 1.3B is producing
end-to-end. Adding ``wan2.1-14b`` here without the model fitting in
~28GB peak VRAM on a 32GB card would create a regression footgun;
the second provider gets its own file when the 14B path is needed.

Strategy: HTTP POST to a dedicated Wan inference server. Standing up
the server itself is operator-side scope (Ollama-style sidecar); the
provider fails with a clear, actionable error message when the server
is unreachable, never silently. Mirrors
:mod:`services.image_providers.flux_schnell` so a single sidecar
operator runbook can drive both providers.

Config (``plugin.video_provider.wan2.1-1.3b`` in app_settings — also
accepts these keys via the dispatcher's per-call forwarding):

- ``enabled`` (default true)
- ``server_url`` (default ``http://host.docker.internal:9840``)
- ``negative_prompt`` (default reads
  ``app_settings.video_negative_prompt``)
- ``output_path`` — where to write the MP4. When absent a tempfile is
  used. The dispatcher always passes ``output_path`` so callers get
  the file at the path they asked for.
- ``num_inference_steps`` (default 50 — Wan 2.1 is full-precision
  diffusion, not a distilled fast model)
- ``guidance_scale`` (default 5.0 — Wan 2.1 paper default)
- ``duration_s`` (default 5 — short clips fit comfortably in 32GB)
- ``width`` / ``height`` (default 832 x 480 — Wan 2.1 1.3B native)
- ``fps`` (default 16 — Wan 2.1 native framerate)
- ``upload_to`` — ``""`` / ``"r2"`` / ``"cloudinary"``. Same shape as
  the image providers; the VideoResult.file_url reflects the upload.

Kind: ``"generate"``.
"""

from __future__ import annotations

import asyncio
import logging
import os
import shutil
import tempfile
from typing import Any

import httpx

from plugins.video_provider import VideoResult

logger = logging.getLogger(__name__)


# Default inference-server URL. Lives at a different port from the
# slideshow video server (9837) and from the SDXL/FLUX image servers
# (9836/9838) so all four can run side-by-side during the A/B period.
_DEFAULT_SERVER_URL = "http://host.docker.internal:9840"

# Wan 2.1 1.3B defaults. The model is full-precision diffusion (not a
# distilled fast model) so steps + guidance are higher than the SDXL
# Lightning / FLUX.1-schnell defaults.
_DEFAULT_STEPS = 50
_DEFAULT_GUIDANCE = 5.0
_DEFAULT_DURATION_S = 5
_DEFAULT_WIDTH = 832
_DEFAULT_HEIGHT = 480
_DEFAULT_FPS = 16

# Per-call HTTP cap. Wan 2.1 1.3B at 50 inference steps renders 5s of
# video in ~3min on a 5090 (warm). Cold-start adds another 30-60s
# under normal conditions, but if the page cache is cold (post WSL
# restart, etc.) the model checkpoints take 5-10min to read off disk.
# Set the timeout well above that worst case so the first call doesn't
# silently time out and force the strategy to fall through to a
# different provider for the rest of the run. Real ceiling is wall-
# clock-budget concern, not a hung server — wan-server's own GPU lock
# guarantees only one /generate runs at a time.
_HTTP_TIMEOUT = httpx.Timeout(900.0, connect=10.0)


def _write_video_bytes(path: str, content: bytes) -> None:
    """Sync helper for ``asyncio.to_thread`` — writes server response
    bytes to disk. Wan 2.1 videos are 1-10 MB; a blocking ``open()`` at
    that size would stall the event loop under concurrent load
    (ASYNC230).
    """
    with open(path, "wb") as f:
        f.write(content)


class Wan21Provider:
    """Wan 2.1 T2V 1.3B text-to-video via dedicated inference server."""

    name = "wan2.1-1.3b"
    kind = "generate"

    async def fetch(
        self,
        query_or_prompt: str,
        config: dict[str, Any],
    ) -> list[VideoResult]:
        prompt = (query_or_prompt or "").strip()
        if not prompt:
            return []

        # Phase H step 5 (GH#95): site_config arrives via the
        # dispatcher's reserved ``_site_config`` key. Mirror
        # FluxSchnellProvider — log a warning when missing and fall
        # through to library defaults so dev calls still work.
        site_config = config.get("_site_config")
        if site_config is None:
            logger.warning(
                "[Wan21Provider] config missing '_site_config' key; "
                "video_service dispatcher hasn't seeded it (GH#95)",
            )

        server_url = _resolve_server_url(config, site_config)
        negative = _resolve_negative(config, site_config)

        steps_val = config.get("num_inference_steps")
        guidance_val = config.get("guidance_scale")
        duration_val = config.get("duration_s")
        width_val = config.get("width")
        height_val = config.get("height")
        fps_val = config.get("fps")

        steps = int(steps_val) if steps_val is not None else _DEFAULT_STEPS
        guidance = (
            float(guidance_val) if guidance_val is not None else _DEFAULT_GUIDANCE
        )
        duration = (
            int(duration_val) if duration_val is not None else _DEFAULT_DURATION_S
        )
        width = int(width_val) if width_val is not None else _DEFAULT_WIDTH
        height = int(height_val) if height_val is not None else _DEFAULT_HEIGHT
        fps = int(fps_val) if fps_val is not None else _DEFAULT_FPS

        output_path = str(config.get("output_path", "") or "")
        cleanup_on_failure = False
        if not output_path:
            with tempfile.NamedTemporaryFile(suffix=".mp4", delete=False) as tmp:
                output_path = tmp.name
            cleanup_on_failure = True

        success = await _generate_to_path(
            prompt=prompt,
            negative=negative,
            output_path=output_path,
            server_url=server_url,
            steps=steps,
            guidance=guidance,
            duration=duration,
            width=width,
            height=height,
            fps=fps,
            site_config=site_config,
        )

        if not success or not os.path.exists(output_path):
            if cleanup_on_failure and os.path.exists(output_path):
                try:
                    os.remove(output_path)
                except OSError:
                    pass
            return []

        file_size = 0
        try:
            file_size = os.path.getsize(output_path)
        except OSError:
            pass

        url = f"file://{output_path}"
        upload_target = str(config.get("upload_to", "") or "")
        if upload_target == "cloudinary":
            try:
                url = await _upload_to_cloudinary(output_path, prompt, site_config)
            except Exception as e:
                logger.warning(
                    "[Wan21Provider] Cloudinary upload failed "
                    "(serving file:// URL): %s", e,
                )
        elif upload_target == "r2":
            try:
                url = await _upload_to_r2(output_path, prompt, site_config)
            except Exception as e:
                logger.warning(
                    "[Wan21Provider] R2 upload failed "
                    "(serving file:// URL): %s", e,
                )

        return [
            VideoResult(
                file_url=url,
                file_path=output_path,
                duration_s=duration,
                width=width,
                height=height,
                fps=fps,
                codec="h264",
                format="mp4",
                source=self.name,
                prompt=prompt,
                metadata={
                    "local_path": output_path,
                    "file_size_bytes": file_size,
                    "negative_prompt": negative,
                    "upload_target": upload_target or "none",
                    "steps": steps,
                    "guidance_scale": guidance,
                    "model": "wan2.1-1.3b",
                    "model_repo": "Wan-AI/Wan2.1-T2V-1.3B",
                    "license": "apache-2.0",
                    "server_url": server_url,
                },
            ),
        ]


# ---------------------------------------------------------------------------
# Config resolution — small helpers so tests can poke at them directly
# ---------------------------------------------------------------------------


def _resolve_server_url(config: dict[str, Any], site_config: Any) -> str:
    """Pick the Wan inference server URL.

    Resolution order:

    1. ``config['server_url']`` (per-call override from the dispatcher).
    2. ``site_config.wan_server_url`` (top-level fast path).
    3. ``site_config.plugin.video_provider.wan2.1-1.3b.server_url``
       (canonical per-install plugin namespace, per GH#124).
    4. Module default ``_DEFAULT_SERVER_URL``.
    """
    direct = str(config.get("server_url", "") or "")
    if direct:
        return direct

    if site_config is None:
        return _DEFAULT_SERVER_URL

    try:
        flat = site_config.get("wan_server_url", "") or ""
    except Exception:
        flat = ""
    if flat:
        return str(flat)

    try:
        nested = site_config.get(
            "plugin.video_provider.wan2.1-1.3b.server_url", "",
        ) or ""
    except Exception:
        nested = ""
    if nested:
        return str(nested)

    return _DEFAULT_SERVER_URL


def _resolve_negative(config: dict[str, Any], site_config: Any) -> str:
    """Pick the negative prompt — config wins, else
    ``video_negative_prompt`` from site_config (parallel to the image
    providers' ``image_negative_prompt`` key).
    """
    direct = str(config.get("negative_prompt", "") or "")
    if direct:
        return direct
    if site_config is None:
        return ""
    try:
        return str(site_config.get("video_negative_prompt", "") or "")
    except Exception:
        return ""


# ---------------------------------------------------------------------------
# Generation strategy — single HTTP call, two response shapes
# ---------------------------------------------------------------------------


async def _generate_to_path(
    *,
    prompt: str,
    negative: str,
    output_path: str,
    server_url: str,
    steps: int,
    guidance: float,
    duration: int,
    width: int,
    height: int,
    fps: int,
    site_config: Any = None,
) -> bool:
    """POST the prompt to the Wan inference server; write the resulting
    MP4 to ``output_path``. Returns True when the file was written.

    Handles both response formats the SDXL/FLUX sidecars use, so a single
    sidecar operator runbook can describe all four servers:

    - ``video/mp4`` (or any ``video/*``) — raw MP4 body. Written
      directly.
    - ``application/json`` — ``{"video_path": "<host path>"}``. The
      sidecar wrote the file to its own filesystem; we map
      ``host_home`` → the worker's ``~`` and copy.

    On unreachable server / non-200 status / parse failures, logs a
    clear, actionable error and returns False so the dispatcher knows
    to fall back to another provider.
    """
    try:
        async with httpx.AsyncClient(timeout=_HTTP_TIMEOUT) as client:
            resp = await client.post(
                f"{server_url}/generate",
                json={
                    "prompt": prompt,
                    "negative_prompt": negative,
                    "steps": steps,
                    "guidance_scale": guidance,
                    "duration_s": duration,
                    "width": width,
                    "height": height,
                    "fps": fps,
                    "model": "wan2.1-1.3b",
                },
                timeout=300,
            )
    except Exception as e:
        # Server unreachable / connection refused / DNS / TLS — log a
        # clear error so operators know to bring up the inference
        # server. NOT silent: this is the documented failure mode per
        # GH#124 acceptance criteria + the no-silent-defaults rule.
        logger.error(
            "[Wan21Provider] inference server unreachable at %s: %s. "
            "Stand up the Wan 2.1 1.3B inference server (default port "
            "9840) or set plugin.video_provider.wan2.1-1.3b.server_url. "
            "Until then, leave app_settings.video_engine on the legacy "
            "engine to avoid pipeline regressions.",
            server_url, e,
        )
        return False

    ct = resp.headers.get("content-type", "")

    if resp.status_code == 200 and ct.startswith("video/"):
        await asyncio.to_thread(_write_video_bytes, output_path, resp.content)
        elapsed = resp.headers.get("X-Elapsed-Seconds", "?")
        logger.info(
            "[Wan21Provider] video generated in %ss: %s",
            elapsed, output_path,
        )
        return True

    if resp.status_code == 200 and ct.startswith("application/json"):
        if await asyncio.to_thread(
            _materialize_sidecar_json, resp, output_path, site_config,
        ):
            return True
        return False

    logger.error(
        "[Wan21Provider] inference server returned %s "
        "(content-type=%r): %s",
        resp.status_code, ct, (resp.text or "")[:200],
    )
    return False


def _materialize_sidecar_json(
    resp: httpx.Response,
    output_path: str,
    site_config: Any = None,
) -> bool:
    """Copy the sidecar-generated MP4 at the path advertised in its JSON
    response to the caller's ``output_path``. Returns True on success.

    Mirrors ``services.image_providers.flux_schnell._materialize_sidecar_json``
    — accepts ``video_path`` OR ``file_path`` keys so a single sidecar
    implementation can serve both image + video providers.
    """
    try:
        data = resp.json()
    except Exception as e:
        logger.warning(
            "[Wan21Provider] sidecar JSON parse failed: %s", e,
        )
        return False

    src = str(data.get("video_path", "") or data.get("file_path", "") or "")
    if not src:
        logger.warning(
            "[Wan21Provider] sidecar JSON missing video_path/file_path: %s",
            data,
        )
        return False

    try:
        host_home = (
            site_config.get("host_home", "") or ""
            if site_config is not None
            else ""
        )
    except Exception:
        host_home = ""
    if host_home and src.startswith(host_home):
        src = src.replace(host_home, os.path.expanduser("~"), 1)
    src = src.replace("\\", "/")

    if not os.path.exists(src):
        logger.warning(
            "[Wan21Provider] sidecar file not visible from worker: "
            "src=%s host_home=%r",
            src, host_home,
        )
        return False

    try:
        shutil.copyfile(src, output_path)
    except OSError as e:
        logger.warning(
            "[Wan21Provider] sidecar copy failed: %s -> %s: %s",
            src, output_path, e,
        )
        return False

    logger.info(
        "[Wan21Provider] video materialized from sidecar "
        "(%dms, %dx%d): %s",
        int(data.get("generation_time_ms", 0) or 0),
        int(data.get("width", 0) or 0),
        int(data.get("height", 0) or 0),
        output_path,
    )
    return True


# ---------------------------------------------------------------------------
# Upload helpers — match the FluxSchnellProvider helpers shape, just for
# video assets. Shared upload infrastructure (cloudinary / r2) is reused.
# ---------------------------------------------------------------------------


async def _upload_to_cloudinary(
    path: str, prompt: str, site_config: Any,
) -> str:
    """Upload a generated MP4 to Cloudinary and return the secure URL."""
    import cloudinary
    import cloudinary.uploader

    if site_config is None:
        raise RuntimeError(
            "Cloudinary upload requires site_config; "
            "video_service dispatcher must seed '_site_config' (GH#95)",
        )

    cloudinary.config(
        cloud_name=site_config.get("cloudinary_cloud_name"),
        api_key=await site_config.get_secret("cloudinary_api_key"),
        api_secret=await site_config.get_secret("cloudinary_api_secret"),
    )

    def _upload() -> dict:
        return cloudinary.uploader.upload(
            path,
            folder="generated_videos/",
            resource_type="video",
            tags=["wan2.1-1.3b", "video_provider"],
            context={"alt": prompt[:200]},
        )

    loop = asyncio.get_event_loop()
    result = await loop.run_in_executor(None, _upload)
    url = result.get("secure_url", "")
    if not url:
        raise RuntimeError("Cloudinary returned empty secure_url")
    return str(url)


async def _upload_to_r2(path: str, prompt: str, site_config: Any) -> str:
    """Upload a generated MP4 to R2 via the shared r2_upload_service."""
    from services.r2_upload_service import upload_to_r2

    if site_config is None:
        raise RuntimeError(
            "R2 upload requires site_config; "
            "video_service dispatcher must seed '_site_config' (GH#95)",
        )
    key = f"wan2_1/{os.path.basename(path)}"
    url = await upload_to_r2(path, key, "video/mp4", site_config=site_config)
    if not url:
        raise RuntimeError("r2_upload_service returned empty URL")
    logger.debug(
        "[Wan21Provider] uploaded %s for prompt %r", key, prompt[:40],
    )
    return str(url)
