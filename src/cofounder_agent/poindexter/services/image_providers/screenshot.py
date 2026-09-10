"""ScreenshotProvider — render an allow-listed operator surface to an image.

Poindexter writes about Poindexter. When it does, the honest illustration for
"our task queue scores every draft" is the QA Rails board showing the real
scores — not a diffusion model's impression of a dashboard. Diffusion is
actively the wrong tool here: ``blog-generation/SKILL.md`` already tells the
writer never to ask for a diagram or chart, because SDXL renders axis labels
as garbled glyphs.

This provider closes that gap. The writer places ``[SCREENSHOT: qa-rails]``,
the marker survives into ``image_plans`` as a ``screenshot_target``, and
``content.generate_images`` routes that slot here instead of to image-gen.
The capture reuses ``services.preview_screenshot`` — the same headless
chromium the vision QA rail already drives — then uploads through the shared
object-store service and returns an ordinary ``ImageResult``, so
``content.inject_images`` needs no special case.

**Targets are an allowlist, never a URL from the model.** The marker carries
a target *key*; the URL behind it comes from
``plugin.image_provider.screenshot.targets`` in app_settings. An LLM-authored
marker therefore cannot point the worker's browser at an arbitrary host (the
worker sits on the Docker network with reachable admin surfaces, so a raw-URL
marker would be an SSRF seam), and an unknown key fails loud rather than
silently resolving to something plausible.

Config (``plugin.image_provider.screenshot`` in app_settings):

- ``targets`` — JSON object mapping target key → capture spec. Empty (the
  shipped default) leaves the provider inert. Per-target keys: ``url``
  (required), ``width``, ``height``, ``full_page``, ``wait_ms``, ``alt``.
- ``timeout_ms`` (default 60000)
- ``upload_to`` (default ``"r2"``) — ``"r2"`` or ``"none"`` (``file://``).

Kind: ``"screenshot"`` — neither a catalog search nor a diffusion render.
Nothing branches on ``ImageProvider.kind`` today; it is a label operators see
in ``poindexter plugins list``.

Issue: Glad-Labs/poindexter#1002.
"""

from __future__ import annotations

import json
import logging
import os
import struct
import tempfile
import uuid
from contextlib import suppress
from typing import Any

from plugins.image_provider import ImageResult

logger = logging.getLogger(__name__)

# Capture defaults. Deliberately desktop-width: operator dashboards reflow to
# a useless single column below ~1200px, and the post renders them at
# max-width anyway.
_DEFAULT_WIDTH = 1600
_DEFAULT_HEIGHT = 1000
_DEFAULT_WAIT_MS = 6000
_DEFAULT_TIMEOUT_MS = 60000


class ScreenshotTargetError(RuntimeError):
    """Raised when a marker names a target that is not in the allowlist."""


def png_size(png: bytes) -> tuple[int, int]:
    """Read ``(width, height)`` out of a PNG's IHDR header.

    A ``full_page`` capture is taller than the viewport it was requested with,
    so the requested height is not the image's height. These dimensions end up
    on the ``<img width height>`` attributes and in the ``media_assets`` row,
    and a wrong aspect ratio there means layout shift on the published page —
    so read the truth rather than echoing the request.

    Returns ``(0, 0)`` for anything that isn't a PNG; callers treat that as
    "unknown" rather than failing the capture over a metadata detail.
    """
    # 8-byte signature, then a 4-byte length + b"IHDR", then width/height.
    if len(png) < 24 or png[:8] != b"\x89PNG\r\n\x1a\n" or png[12:16] != b"IHDR":
        return 0, 0
    width, height = struct.unpack(">II", png[16:24])
    return int(width), int(height)


def parse_targets(raw: Any) -> dict[str, dict[str, Any]]:
    """Normalise the ``targets`` setting into ``{key: spec}``.

    Accepts either an already-decoded dict (tests, future typed settings) or
    the JSON string app_settings actually stores. A malformed value raises —
    a screenshot allowlist that silently parses to ``{}`` would turn every
    marker into a "target not configured" mystery.
    """
    if not raw:
        return {}
    decoded = json.loads(raw) if isinstance(raw, str) else raw
    if not isinstance(decoded, dict):
        raise ValueError(
            f"screenshot targets must be a JSON object, got {type(decoded).__name__}",
        )

    targets: dict[str, dict[str, Any]] = {}
    for key, spec in decoded.items():
        # A bare string is shorthand for {"url": "..."} — the common case.
        if isinstance(spec, str):
            spec = {"url": spec}
        if not isinstance(spec, dict):
            raise ValueError(
                f"screenshot target {key!r} must be a URL string or an object",
            )
        if not str(spec.get("url", "") or "").strip():
            raise ValueError(f"screenshot target {key!r} is missing 'url'")
        targets[str(key)] = spec
    return targets


def resolve_target(
    target_key: str, targets: dict[str, dict[str, Any]],
) -> dict[str, Any]:
    """Look ``target_key`` up in the allowlist, or raise with the valid keys.

    The error names every configured key on purpose: this surfaces in a worker
    log after a writer invented a plausible-but-wrong target, and the fix is
    always "use one of these" or "add it to app_settings".
    """
    spec = targets.get(target_key)
    if spec is None:
        known = ", ".join(sorted(targets)) or "(none configured)"
        raise ScreenshotTargetError(
            f"screenshot target {target_key!r} is not in the allowlist; "
            f"configured targets: {known}",
        )
    return spec


class ScreenshotProvider:
    """Capture an allow-listed URL with headless chromium."""

    name = "screenshot"
    kind = "screenshot"

    async def fetch(
        self,
        query_or_prompt: str,
        config: dict[str, Any],
    ) -> list[ImageResult]:
        target_key = (query_or_prompt or "").strip()
        if not target_key:
            return []

        try:
            targets = parse_targets(config.get("targets"))
        except (ValueError, json.JSONDecodeError) as e:
            logger.error(
                "[ScreenshotProvider] targets setting is malformed — no "
                "screenshot can resolve until it is fixed: %s", e,
            )
            return []

        try:
            spec = resolve_target(target_key, targets)
        except ScreenshotTargetError as e:
            logger.error("[ScreenshotProvider] %s", e)
            return []

        url = str(spec["url"]).strip()
        width = int(spec.get("width", _DEFAULT_WIDTH) or _DEFAULT_WIDTH)
        height = int(spec.get("height", _DEFAULT_HEIGHT) or _DEFAULT_HEIGHT)
        full_page = bool(spec.get("full_page", False))
        wait_ms = int(spec.get("wait_ms", _DEFAULT_WAIT_MS) or _DEFAULT_WAIT_MS)
        timeout_ms = int(
            config.get("timeout_ms", _DEFAULT_TIMEOUT_MS) or _DEFAULT_TIMEOUT_MS,
        )
        alt_text = str(spec.get("alt", "") or "").strip() or (
            f"Screenshot of the {target_key} surface"
        )

        from services.preview_screenshot import capture_preview_screenshot

        png = await capture_preview_screenshot(
            url,
            viewport_width=width,
            viewport_height=height,
            full_page=full_page,
            timeout_ms=timeout_ms,
            wait_after_load_ms=wait_ms,
        )
        if not png:
            # capture_preview_screenshot returns None (never raises) when
            # playwright is missing or the page failed — it has already logged
            # the cause. An empty list here makes the slot fall through to the
            # caller's normal "no image for this placeholder" handling.
            logger.warning(
                "[ScreenshotProvider] capture returned nothing for target "
                "%r (%s)", target_key, url,
            )
            return []

        actual_width, actual_height = png_size(png)

        with tempfile.NamedTemporaryFile(
            suffix=".png", prefix=f"shot-{target_key}-", delete=False,
        ) as tmp:
            tmp.write(png)
            local_path = tmp.name

        upload_to = str(config.get("upload_to", "r2") or "r2")
        image_url = f"file://{local_path}"
        if upload_to == "r2":
            try:
                image_url = await _upload(
                    local_path, target_key,
                    site_config=config.get("_site_config"),
                )
            except Exception as e:
                logger.warning(
                    "[ScreenshotProvider] upload failed for %r, serving "
                    "file:// URL: %s", target_key, e,
                )

        return [
            ImageResult(
                url=image_url,
                thumbnail=image_url,
                photographer="Glad Labs",
                photographer_url="",
                width=actual_width or width,
                height=actual_height or height,
                alt_text=alt_text,
                source=self.name,
                search_query=target_key,
                metadata={
                    "screenshot_target": target_key,
                    "captured_url": url,
                    "local_path": local_path,
                    "full_page": full_page,
                },
            ),
        ]


async def _upload(path: str, target_key: str, *, site_config: Any) -> str:
    """Upload a captured PNG through the shared object-store service.

    The service converts PNG → WebP@80 and downscales to fit 1920x1920, so a
    full-page dashboard capture arrives as a ~150 KB image rather than a 1 MB
    PNG.
    """
    from services.r2_upload_service import R2UploadService

    if site_config is None:
        raise RuntimeError(
            "screenshot upload requires site_config; the image dispatcher "
            "must seed '_site_config' (GH#95 / constructor-DI PR 4)",
        )
    svc = R2UploadService(site_config=site_config)
    key = f"images/screenshots/{target_key}-{uuid.uuid4().hex[:8]}.png"
    url = await svc.upload_to_r2(path, key, "image/png")
    if not url:
        raise RuntimeError("r2_upload_service returned empty URL")
    with suppress(OSError):  # silent-ok: temp cleanup, upload already landed
        os.remove(path)
    return url


__all__ = [
    "ScreenshotProvider",
    "ScreenshotTargetError",
    "parse_targets",
    "png_size",
    "resolve_target",
]
