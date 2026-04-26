"""VideoProvider — text-to-video / slideshow-video Protocol for the
short-form + long-form video pipeline.

A VideoProvider takes a text prompt (or a structured render request that
includes pre-generated images + audio) and returns a :class:`VideoResult`
that callers drop into the existing video feed.

Two implementation styles, mirroring the ImageProvider split:

- **Generation providers** (Wan 2.1 1.3B, Wan 14B in a future ticket,
  Mochi, LTX-Video) — true text-to-video models. Render directly from a
  prompt; ignore ``image_paths`` / ``audio_path`` config fields.
- **Composition providers** (Ken Burns slideshow) — assemble pre-rendered
  images + audio into an MP4. Read ``image_paths`` + ``audio_path``
  from config; ignore the prompt argument (or use it as a title).

Both paths produce the same ``list[VideoResult]`` shape so
``video_service`` can swap between them via a single
``app_settings.video_engine`` flip.

Register a VideoProvider via ``pyproject.toml``:

.. code:: toml

    [project.entry-points."poindexter.video_providers"]
    wan2.1-1.3b = "cofounder_agent.services.video_providers.wan2_1:Wan21Provider"
    ken_burns_slideshow = "cofounder_agent.services.video_providers.ken_burns_slideshow:KenBurnsSlideshowProvider"

Per-install config lives in ``app_settings.plugin.video_provider.<name>``
— ``server_url``, warmup flag, render dimensions, etc.

Mirrors ``plugins/image_provider.py`` (GitHub #71 / #123) so a
contributor who has shipped an ImageProvider already knows the shape.
Tracks GitHub #124 — Wan 2.1 T2V 1.3B as the first generation provider.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Protocol, runtime_checkable


@dataclass
class VideoResult:
    """A single rendered video returned by a VideoProvider.

    Field semantics are the union of the legacy ``video_service.VideoResult``
    (file_path, file_size_bytes, duration_seconds) and the metadata that
    a true T2V model surfaces (width, height, fps, codec, format).
    Downstream callers that expect the legacy ``VideoResult`` can build
    one from this via ``to_legacy_dict()``.

    Attributes:
        file_url: Caller-addressable URL or ``file://`` path. Composition
            providers always return a ``file://`` URL pointing at a local
            MP4. Generation providers may return an HTTP URL when the
            inference server hosts the asset directly.
        file_path: Local filesystem path the file was written to. May be
            ``None`` when the provider couldn't materialize the file
            locally (e.g., the inference server returned a remote-only
            URL); callers that need bytes-on-disk should fall back to a
            download step.
        duration_s: Rendered duration in seconds. ``0`` when unknown.
        width: Pixel width. ``None`` when unknown.
        height: Pixel height. ``None`` when unknown.
        fps: Frames per second. ``None`` when unknown.
        codec: Video codec identifier (``"h264"``, ``"hevc"``, ``"vp9"``,
            ``"av1"``). Empty string when unknown.
        format: Container format (``"mp4"``, ``"webm"``). Empty string
            when unknown.
        source: Provider name (``"wan2.1-1.3b"``, ``"ken_burns_slideshow"``,
            etc.). Matches the entry_point key.
        prompt: The text prompt that produced this video (true T2V) or
            the post title (composition providers).
        metadata: Free-form per-provider metadata. Conventionally
            includes ``"file_size_bytes"`` (int), ``"images_used"``
            (int, composition providers), ``"server_url"`` (str), and
            ``"license"`` (str — SPDX identifier of the underlying
            model).
    """

    file_url: str
    file_path: str | None = None
    duration_s: int = 0
    width: int | None = None
    height: int | None = None
    fps: int | None = None
    codec: str = ""
    format: str = ""
    source: str = "unknown"
    prompt: str = ""
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        """Serialize to a plain dict — symmetric with ImageResult."""
        return {
            "file_url": self.file_url,
            "file_path": self.file_path,
            "duration_s": self.duration_s,
            "width": self.width,
            "height": self.height,
            "fps": self.fps,
            "codec": self.codec,
            "format": self.format,
            "source": self.source,
            "prompt": self.prompt,
            "metadata": self.metadata,
        }

    def to_legacy_dict(self) -> dict[str, Any]:
        """Adapt to the legacy ``services.video_service.VideoResult``
        field names (``file_path``, ``duration_seconds``,
        ``file_size_bytes``, ``images_used``, ``error``) so callers
        that haven't been migrated keep working.
        """
        return {
            "success": bool(self.file_path or self.file_url),
            "file_path": self.file_path,
            "duration_seconds": self.duration_s,
            "file_size_bytes": int(self.metadata.get("file_size_bytes", 0) or 0),
            "images_used": int(self.metadata.get("images_used", 0) or 0),
            "error": None,
        }


@runtime_checkable
class VideoProvider(Protocol):
    """Video-source plugin contract.

    Two implementation styles (mirrors ImageProvider's ``search`` /
    ``generate`` split):

    - **Generation providers** synthesize a video from a text prompt.
      Slow — Wan 2.1 1.3B renders ~5s of video in ~30s on a 5090. Callers
      should await with a generous timeout (5 minutes is reasonable).
    - **Composition providers** stitch pre-rendered images + audio into
      a slideshow MP4. Faster (5-30s) but requires the caller to supply
      ``image_paths`` + ``audio_path`` in config.

    Both paths produce the same ``list[VideoResult]`` shape so
    ``video_service`` can swap between them via a single
    ``app_settings.video_engine`` flip.

    Attributes:
        name: Unique plugin name (matches the entry_point key + the
            ``source`` label attached to each VideoResult).
        kind: Either ``"generate"`` (true T2V) or ``"compose"``
            (slideshow / image+audio assembly). ``video_service`` uses
            this to decide whether to run the SDXL+TTS prep pipeline
            before dispatching.
    """

    name: str
    kind: str  # "generate" or "compose"

    async def fetch(
        self,
        query_or_prompt: str,
        config: dict[str, Any],
    ) -> list[VideoResult]:
        """Return zero or more VideoResult instances.

        Args:
            query_or_prompt: For generation providers, the text-to-video
                prompt. For composition providers, the post title (used
                as the video title overlay; the actual visual content
                comes from ``config['image_paths']``).
            config: Per-install config from
                ``app_settings.plugin.video_provider.<name>`` plus
                per-call overrides from ``video_service``. Conventional
                keys:

                - ``output_path`` (str): where to write the MP4.
                - ``_site_config`` (SiteConfig): DI seam for providers
                  that need to read other app_settings.
                - ``image_paths`` (list[str]): composition-only.
                - ``audio_path`` (str): composition-only.
                - ``duration_s`` (int): generation-only target duration.
                - ``width`` / ``height`` (int): output dimensions.
                - ``fps`` (int): output framerate.
                - ``negative_prompt`` (str): generation-only.
                - ``upload_to`` (str): ``""`` / ``"r2"`` / ``"cloudinary"``.

        Returns:
            list[VideoResult]. Empty list when the provider has no
            matching result (server unreachable, model not ready,
            empty input). Providers SHOULD log the failure with
            enough detail for an operator to diagnose, then return
            ``[]`` so the caller can fall back to another provider.
            Genuine config errors (missing API key, invalid setting)
            SHOULD raise so the dispatcher fails loud per the
            no-silent-defaults convention.
        """
        ...
