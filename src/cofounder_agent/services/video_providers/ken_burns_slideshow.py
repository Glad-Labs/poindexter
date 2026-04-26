"""KenBurnsSlideshowProvider — VideoProvider wrapper around the legacy
slideshow video pipeline (SDXL frames + podcast audio + Ken Burns
zoom/pan, assembled by the host video server on port 9837).

Tracks GitHub #124 — exists so the cutover from the legacy pipeline to
Wan 2.1 1.3B is a single ``app_settings.video_engine`` flip rather than
a code change. The provider is a thin adapter: it delegates to
:func:`services.video_service.generate_video_for_post` and translates
the legacy ``VideoResult`` dataclass into the plugin
:class:`VideoResult <plugins.video_provider.VideoResult>`.

**Why a wrapper, not a duplicate?** The legacy pipeline has been in
production for weeks and has working ffmpeg/SDXL/edge-TTS plumbing.
Reimplementing it as a provider would duplicate ~600 lines of code for
no benefit. The wrapper lets the new VideoProvider Protocol drive the
existing engine until somebody decides the slideshow path is dead and
deletes it.

Config (``plugin.video_provider.ken_burns_slideshow`` in app_settings —
also accepts these keys via the dispatcher's per-call forwarding):

- ``post_id`` (required): the post identifier — used as the output
  filename and to look up the matching podcast audio.
- ``content`` (str): post body. The legacy pipeline mines it for
  reusable images.
- ``podcast_path`` (str, optional): path to the narration MP3.
- ``pre_generated_scenes`` (list[str], optional): SDXL prompts already
  written by the writer model.
- ``output_path`` (str, optional): where to write the MP4. Ignored —
  the legacy pipeline writes to its hardcoded
  ``~/.poindexter/video/<post_id>.mp4``.
- ``short`` (bool, default false): when true, dispatches to
  ``generate_short_video_for_post`` for a 1080x1920 vertical Shorts
  build.
- ``force`` (bool, default false): regenerate even if the file
  already exists.
- ``_site_config`` (SiteConfig): DI seam — required because the legacy
  pipeline reaches into multiple ``app_settings`` keys.

Kind: ``"compose"``.
"""

from __future__ import annotations

import logging
import os
from typing import Any

from plugins.video_provider import VideoResult

logger = logging.getLogger(__name__)


class KenBurnsSlideshowProvider:
    """Legacy slideshow pipeline exposed as a VideoProvider."""

    name = "ken_burns_slideshow"
    kind = "compose"

    async def fetch(
        self,
        query_or_prompt: str,
        config: dict[str, Any],
    ) -> list[VideoResult]:
        # The legacy pipeline takes title, not prompt — for a slideshow
        # the "prompt" arg is just the title overlay.
        title = (query_or_prompt or "").strip()
        if not title:
            return []

        post_id = str(config.get("post_id", "") or "")
        if not post_id:
            logger.error(
                "[KenBurnsSlideshowProvider] config missing 'post_id' — "
                "the legacy pipeline keys output filenames on post_id. "
                "video_service dispatcher must populate it.",
            )
            return []

        site_config = config.get("_site_config")
        if site_config is None:
            logger.error(
                "[KenBurnsSlideshowProvider] config missing '_site_config' "
                "key — the legacy pipeline reads multiple app_settings "
                "and won't work without it (GH#95).",
            )
            return []

        content = str(config.get("content", "") or "")
        podcast_path = config.get("podcast_path")
        if podcast_path is not None:
            podcast_path = str(podcast_path)
        pre_generated_scenes = config.get("pre_generated_scenes")
        force = bool(config.get("force", False))
        is_short = bool(config.get("short", False))

        # Lazy import — services.video_service imports site_config
        # plumbing on its own, and circular imports between
        # video_service ↔ video_providers.ken_burns_slideshow are easy
        # to introduce if either side imports the other at module level.
        if is_short:
            from services.video_service import generate_short_video_for_post

            legacy_result = await generate_short_video_for_post(
                post_id=post_id,
                title=title,
                content=content,
                podcast_path=podcast_path,
                pre_generated_scenes=pre_generated_scenes,
                force=force,
                site_config=site_config,
            )
        else:
            from services.video_service import generate_video_for_post

            legacy_result = await generate_video_for_post(
                post_id=post_id,
                title=title,
                content=content,
                podcast_path=podcast_path,
                pre_generated_scenes=pre_generated_scenes,
                force=force,
                site_config=site_config,
            )

        if not legacy_result.success or not legacy_result.file_path:
            logger.warning(
                "[KenBurnsSlideshowProvider] legacy pipeline returned "
                "no file: error=%r",
                legacy_result.error,
            )
            return []

        file_path = legacy_result.file_path
        file_url = f"file://{file_path}"
        file_size = (
            int(legacy_result.file_size_bytes)
            if legacy_result.file_size_bytes
            else (os.path.getsize(file_path) if os.path.exists(file_path) else 0)
        )

        # Slideshow pipeline produces 1920x1080 (or 1080x1920 for shorts)
        # MP4s at 30fps via ffmpeg's libx264 — emit those as defaults so
        # downstream callers don't have to probe the file.
        width = 1080 if is_short else 1920
        height = 1920 if is_short else 1080

        return [
            VideoResult(
                file_url=file_url,
                file_path=file_path,
                duration_s=int(legacy_result.duration_seconds or 0),
                width=width,
                height=height,
                fps=30,
                codec="h264",
                format="mp4",
                source=self.name,
                prompt=title,
                metadata={
                    "local_path": file_path,
                    "file_size_bytes": file_size,
                    "images_used": int(legacy_result.images_used or 0),
                    "post_id": post_id,
                    "is_short": is_short,
                    "model": "ken_burns_slideshow",
                    "license": "n/a",
                },
            ),
        ]
