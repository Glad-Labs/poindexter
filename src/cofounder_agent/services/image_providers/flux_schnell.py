"""FluxSchnellProvider — text-to-image via FLUX.1-schnell inference server.

A second-generation generation provider sibling to
:class:`SdxlProvider <services.image_providers.sdxl.SdxlProvider>`.
Tracks GitHub issue #123 — A/B testing FLUX.1-schnell against
SDXL Lightning for prompt adherence and text-in-image quality.

License: **Apache-2.0** (FLUX.1-schnell). The non-commercial
**FLUX.1-dev** variant is intentionally NOT supported here — adding a
``flux_dev`` provider would create a license-laundering footgun. Use
``schnell`` or wait for an explicit commercial license decision.

Strategy: HTTP POST to a dedicated FLUX inference server (default
``http://host.docker.internal:9838``). Mirrors the SDXL host-sidecar
contract (``image/*`` raw bytes OR ``application/json`` with
``image_path``) so the same operational tooling applies. Standing up
the inference server itself is an infra task — the provider fails with
a clear error message when the server is unreachable, never silently.

Config (``plugin.image_provider.flux_schnell`` in app_settings — also
accepts these keys via the dispatcher's per-call forwarding):

- ``enabled`` (default true)
- ``server_url`` (default ``http://host.docker.internal:9838``)
- ``negative_prompt`` (default reads ``app_settings.image_negative_prompt``)
- ``output_path`` — where to write the PNG. When absent a tempfile is
  used. The dispatcher always passes ``output_path`` so callers get
  the file at the path they asked for.
- ``num_inference_steps`` (default 4 — FLUX.1-schnell is 4-step distilled)
- ``guidance_scale`` (default 0.0 — schnell ignores guidance by design)
- ``task_id`` — WebSocket progress stream identifier
- ``upload_to`` — ``""`` / ``"cloudinary"`` / ``"r2"``. Same shape as
  SdxlProvider; the ImageResult.url reflects the upload target.

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

from plugins.image_provider import ImageResult

logger = logging.getLogger(__name__)


# Default inference-server URL. Lives at a different port from the SDXL
# sidecar so both can run side-by-side during the A/B period.
_DEFAULT_SERVER_URL = "http://host.docker.internal:9838"

# FLUX.1-schnell defaults — 4-step distillation, guidance disabled.
_DEFAULT_STEPS = 4
_DEFAULT_GUIDANCE = 0.0

# Per-call HTTP cap. FLUX.1-schnell renders in ~2-4s on a 5090; allow
# generous headroom for model cold-start + retries.
_HTTP_TIMEOUT = httpx.Timeout(90.0, connect=5.0)


def _write_image_bytes(path: str, content: bytes) -> None:
    """Sync helper for ``asyncio.to_thread`` — writes server response
    bytes to disk. FLUX images are 1-3 MB; a blocking ``open()`` at that
    size would stall the event loop under concurrent load (ASYNC230).
    """
    with open(path, "wb") as f:
        f.write(content)


class FluxSchnellProvider:
    """FLUX.1-schnell text-to-image via dedicated inference server."""

    name = "flux_schnell"
    kind = "generate"

    async def fetch(
        self,
        query_or_prompt: str,
        config: dict[str, Any],
    ) -> list[ImageResult]:
        prompt = (query_or_prompt or "").strip()
        if not prompt:
            return []

        # Phase H step 5 (GH#95): site_config is resolved from the
        # dispatcher's reserved ``_site_config`` key. Mirror SdxlProvider
        # — log a warning when the dispatcher hasn't seeded it and fall
        # through to per-call config + library defaults.
        site_config = config.get("_site_config")
        if site_config is None:
            logger.warning(
                "[FluxSchnellProvider] config missing '_site_config' key; "
                "image_service dispatcher hasn't been migrated yet (GH#95)",
            )

        server_url = _resolve_server_url(config, site_config)
        negative = _resolve_negative(config, site_config)
        steps_val = config.get("num_inference_steps")
        guidance_val = config.get("guidance_scale")
        steps = int(steps_val) if steps_val is not None else _DEFAULT_STEPS
        guidance = (
            float(guidance_val) if guidance_val is not None else _DEFAULT_GUIDANCE
        )
        task_id = config.get("task_id")

        output_path = str(config.get("output_path", "") or "")
        cleanup_on_failure = False
        if not output_path:
            with tempfile.NamedTemporaryFile(suffix=".png", delete=False) as tmp:
                output_path = tmp.name
            cleanup_on_failure = True

        success = await _generate_to_path(
            prompt=prompt,
            negative=negative,
            output_path=output_path,
            server_url=server_url,
            steps=steps,
            guidance=guidance,
            site_config=site_config,
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
                url = await _upload_to_cloudinary(output_path, prompt, site_config)
            except Exception as e:
                logger.warning(
                    "[FluxSchnellProvider] Cloudinary upload failed "
                    "(serving file:// URL): %s", e,
                )
        elif upload_target == "r2":
            try:
                url = await _upload_to_r2(output_path, prompt, site_config)
            except Exception as e:
                logger.warning(
                    "[FluxSchnellProvider] R2 upload failed "
                    "(serving file:// URL): %s", e,
                )

        return [
            ImageResult(
                url=url,
                thumbnail=url,
                photographer="Glad Labs FLUX.1-schnell",
                photographer_url="",
                alt_text=prompt[:200],
                source=self.name,
                search_query=prompt,
                metadata={
                    "local_path": output_path,
                    "negative_prompt": negative,
                    "upload_target": upload_target or "none",
                    "steps": steps,
                    "guidance_scale": guidance,
                    "model": "flux_schnell",
                    "license": "apache-2.0",
                    "server_url": server_url,
                    "task_id": str(task_id) if task_id is not None else "",
                },
            ),
        ]


# ---------------------------------------------------------------------------
# Config resolution — small helpers so tests can poke at them directly
# ---------------------------------------------------------------------------


def _resolve_server_url(config: dict[str, Any], site_config: Any) -> str:
    """Pick the FLUX inference server URL.

    Resolution order:

    1. ``config['server_url']`` (per-call override from the dispatcher).
    2. ``site_config.flux_schnell_server_url`` (top-level fast path).
    3. ``site_config.plugin.image_provider.flux_schnell.server_url``
       (canonical per-install plugin namespace).
    4. Module default ``_DEFAULT_SERVER_URL``.
    """
    direct = str(config.get("server_url", "") or "")
    if direct:
        return direct

    if site_config is None:
        return _DEFAULT_SERVER_URL

    try:
        flat = site_config.get("flux_schnell_server_url", "") or ""
    except Exception:
        flat = ""
    if flat:
        return str(flat)

    try:
        nested = site_config.get(
            "plugin.image_provider.flux_schnell.server_url", "",
        ) or ""
    except Exception:
        nested = ""
    if nested:
        return str(nested)

    return _DEFAULT_SERVER_URL


def _resolve_negative(config: dict[str, Any], site_config: Any) -> str:
    """Pick the negative prompt — config wins, else the site_config
    fallback ``image_negative_prompt`` (matching SdxlProvider)."""
    direct = str(config.get("negative_prompt", "") or "")
    if direct:
        return direct
    if site_config is None:
        return ""
    try:
        return str(site_config.get("image_negative_prompt", "") or "")
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
    site_config: Any = None,
) -> bool:
    """POST the prompt to the FLUX inference server; write the resulting
    PNG to ``output_path``. Returns True when the file was written.

    Handles both response formats the SDXL sidecar uses (so a single
    operator-written sidecar can drive both providers if desired):

    - ``image/*`` — raw PNG body. Written directly.
    - ``application/json`` — ``{"image_path": "<host path>"}``. The
      sidecar wrote the file to its own filesystem; we map
      ``host_home`` → the worker's ``~`` and copy.

    On unreachable server / non-200 status / parse failures, logs a
    clear error and returns False so callers know to fall back to
    another provider.
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
                    "model": "flux_schnell",
                },
                timeout=90,
            )
    except Exception as e:
        # Server unreachable / connection refused / DNS / TLS — log a
        # clear error so operators know to bring up the inference
        # server. NOT silent: this is the documented failure mode per
        # GH#123 acceptance criteria.
        logger.error(
            "[FluxSchnellProvider] inference server unreachable at %s: %s. "
            "Stand up the FLUX.1-schnell inference server (default port "
            "9838) or set plugin.image_provider.flux_schnell.server_url.",
            server_url, e,
        )
        return False

    ct = resp.headers.get("content-type", "")

    if resp.status_code == 200 and ct.startswith("image/"):
        await asyncio.to_thread(_write_image_bytes, output_path, resp.content)
        elapsed = resp.headers.get("X-Elapsed-Seconds", "?")
        logger.info(
            "[FluxSchnellProvider] image generated in %ss: %s",
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
        "[FluxSchnellProvider] inference server returned %s "
        "(content-type=%r): %s",
        resp.status_code, ct, (resp.text or "")[:200],
    )
    return False


def _materialize_sidecar_json(
    resp: httpx.Response,
    output_path: str,
    site_config: Any = None,
) -> bool:
    """Copy the sidecar-generated PNG at the path advertised in its JSON
    response to the caller's ``output_path``. Returns True on success.

    Mirrors ``services.image_providers.sdxl._materialize_sidecar_json``
    so a single sidecar implementation can serve both providers.
    """
    try:
        data = resp.json()
    except Exception as e:
        logger.warning(
            "[FluxSchnellProvider] sidecar JSON parse failed: %s", e,
        )
        return False

    src = str(data.get("image_path", "") or "")
    if not src:
        logger.warning(
            "[FluxSchnellProvider] sidecar JSON missing image_path: %s", data,
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
            "[FluxSchnellProvider] sidecar file not visible from worker: "
            "src=%s host_home=%r",
            src, host_home,
        )
        return False

    try:
        shutil.copyfile(src, output_path)
    except OSError as e:
        logger.warning(
            "[FluxSchnellProvider] sidecar copy failed: %s -> %s: %s",
            src, output_path, e,
        )
        return False

    logger.info(
        "[FluxSchnellProvider] image materialized from sidecar "
        "(%dms, %dpx): %s",
        int(data.get("generation_time_ms", 0) or 0),
        int(data.get("width", 0) or 0),
        output_path,
    )
    return True


# ---------------------------------------------------------------------------
# Upload helpers — same contract as SdxlProvider's helpers. Shared upload
# infrastructure (cloudinary / r2_upload_service) is reused so this
# provider doesn't drift from the legacy SDXL upload behavior.
# ---------------------------------------------------------------------------


async def _upload_to_cloudinary(
    path: str, prompt: str, site_config: Any,
) -> str:
    """Upload a generated PNG to Cloudinary and return the secure URL."""
    import cloudinary
    import cloudinary.uploader

    if site_config is None:
        raise RuntimeError(
            "Cloudinary upload requires site_config; "
            "image_service dispatcher must seed '_site_config' (GH#95)",
        )

    cloudinary.config(
        cloud_name=site_config.get("cloudinary_cloud_name"),
        api_key=await site_config.get_secret("cloudinary_api_key"),
        api_secret=await site_config.get_secret("cloudinary_api_secret"),
    )

    def _upload() -> dict:
        return cloudinary.uploader.upload(
            path,
            folder="generated/",
            resource_type="image",
            tags=["flux_schnell", "provider"],
            context={"alt": prompt[:200]},
        )

    loop = asyncio.get_event_loop()
    result = await loop.run_in_executor(None, _upload)
    url = result.get("secure_url", "")
    if not url:
        raise RuntimeError("Cloudinary returned empty secure_url")
    return str(url)


async def _upload_to_r2(path: str, prompt: str, site_config: Any) -> str:
    """Upload a generated PNG to R2 via the shared r2_upload_service."""
    from services.r2_upload_service import upload_to_r2

    if site_config is None:
        raise RuntimeError(
            "R2 upload requires site_config; "
            "image_service dispatcher must seed '_site_config' (GH#95)",
        )
    key = f"flux_schnell/{os.path.basename(path)}"
    url = await upload_to_r2(path, key, "image/png", site_config=site_config)
    if not url:
        raise RuntimeError("r2_upload_service returned empty URL")
    logger.debug(
        "[FluxSchnellProvider] uploaded %s for prompt %r", key, prompt[:40],
    )
    return str(url)
