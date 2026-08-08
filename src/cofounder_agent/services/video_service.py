"""Video helpers — shared image-gen frame handling + the durable video dir.

The legacy ``:9837`` host slideshow lane — ``generate_video_for_post`` /
``generate_short_video_for_post`` and the ``scripts/video-server.py`` host
process they POSTed to — was retired 2026-07. The live media pipeline renders
through ``services/video_renderers/shot_list_renderer.render_shot_list``
(per-shot image-gen / Wan i2v clips assembled by ``FFmpegLocalCompositor``),
so this module now carries only the two pieces that path still shares:

- ``VIDEO_DIR`` — the durable ``~/.poindexter/video`` output dir (read by the
  media-persist atom, the video routes/feed, and the CMS ``has_video`` check).
- ``_consume_image_gen_response`` — materialises an image-gen frame from either
  the raw-bytes or JSON response shape (used by the shot-list renderer).
"""

import asyncio
import os
from pathlib import Path

import httpx

from services.logger_config import get_logger

logger = get_logger(__name__)

VIDEO_DIR = Path(os.path.expanduser("~")) / ".poindexter" / "video"


def _write_bytes(path: str, content: bytes) -> None:
    """Sync file-write helper suitable for ``asyncio.to_thread``.

    Used to write downloaded image-gen frames without blocking the event loop
    (ASYNC230). Binary mode; caller supplies the full bytes payload.
    """
    with open(path, "wb") as f:
        f.write(content)


async def _consume_image_gen_response(
    resp: httpx.Response,
    *,
    image_gen_url: str,
    output_path: str,
    frame_label: str,
) -> str | None:
    """Materialise image-gen image bytes from either response shape.

    The image-gen server returns either:

    - **Raw image bytes** (``Content-Type: image/png``) — older behaviour;
      caller writes the bytes directly to ``output_path``.
    - **JSON** (``Content-Type: application/json``) with
      ``{"filename": "img_<hash>.png", "image_path": ...}`` — current
      behaviour. The image is sitting on the image-gen container's disk; the
      worker fetches it via ``GET <image_gen_url>/images/<filename>`` (matches
      the helper for the featured-image path, see
      ``modules/content/stages/source_featured_image._download_featured_gen_image``).

    Prior to 2026-05-20, the video-render path only handled the ``image/*``
    branch — every JSON response was logged as a failure with "image-gen
    returned 200 for frame N" and the image path was discarded, so every
    frame per cycle would silently fail. Closes Glad-Labs/poindexter#198
    follow-up (the underlying ``poindexter#459`` fix was already applied to
    the featured-image path; this extends it to the shot-render path).
    """
    if resp.status_code != 200:
        body = resp.text[:200] if resp.text else "(empty)"
        logger.warning(
            "[VIDEO] image-gen returned %d for %s: %s",
            resp.status_code, frame_label, body,
        )
        return None
    ct = resp.headers.get("content-type", "")
    if ct.startswith("image/"):
        await asyncio.to_thread(_write_bytes, output_path, resp.content)
        return output_path
    if ct.startswith("application/json"):
        try:
            data = resp.json()
        except Exception as exc:  # noqa: BLE001 — defensive
            logger.warning(
                "[VIDEO] image-gen returned non-JSON for %s: %s", frame_label, exc,
            )
            return None
        filename = data.get("filename") or os.path.basename(
            data.get("image_path", "") or "",
        )
        if not filename:
            logger.warning(
                "[VIDEO] image-gen JSON missing filename/image_path for %s: %s",
                frame_label, str(data)[:120],
            )
            return None
        safe_name = os.path.basename(filename)
        url = f"{image_gen_url.rstrip('/')}/images/{safe_name}"
        try:
            async with httpx.AsyncClient(
                timeout=httpx.Timeout(30.0, connect=5.0),
            ) as fetch_client:
                fetch_resp = await fetch_client.get(url)
        except Exception as exc:  # noqa: BLE001
            logger.warning(
                "[VIDEO] image-gen /images fetch failed for %s: %s",
                safe_name, exc,
            )
            return None
        if fetch_resp.status_code != 200:
            logger.warning(
                "[VIDEO] image-gen /images returned %d for %s",
                fetch_resp.status_code, safe_name,
            )
            return None
        await asyncio.to_thread(
            _write_bytes, output_path, fetch_resp.content,
        )
        return output_path
    body = resp.text[:200] if resp.text else "(empty)"
    logger.warning(
        "[VIDEO] image-gen unknown content-type %r for %s: %s",
        ct, frame_label, body,
    )
    return None
