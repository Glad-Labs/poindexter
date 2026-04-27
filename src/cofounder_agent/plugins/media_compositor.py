"""MediaCompositor — assemble scenes / audio / captions into a
finished media file.

The video pipeline's ``stitch`` Stage drives a media compositor to
turn:

  - per-scene visual clips (from VideoProvider / ImageProvider+KB),
  - per-scene narration audio (from TTSProvider),
  - a soundtrack track (from AudioGenProvider),
  - optional caption overlays (from CaptionProvider),

…into one publishable MP4. That work is "outside Poindexter" — it
calls a local ffmpeg binary or, in a future world, a cloud
encoder (Mux, Cloudinary, AWS MediaConvert). Wrapping it as a
Protocol keeps that swap cheap.

Two implementation styles in mind:

- **Local-binary compositors** (ffmpeg via subprocess) — talk to a
  process on the host. Cost is electricity; ``is_local=True`` on
  the cost-guard record. Default first-line implementation.
- **Cloud compositors** (Mux, Cloudinary, AWS MediaConvert) —
  upload inputs, queue a render job, poll for completion, fetch
  output. Cost is dollars; ``is_local=False``. Useful at scale or
  when the operator wants HEVC / AV1 transcoding the local box
  isn't sized for.

Register a MediaCompositor via ``pyproject.toml``:

.. code:: toml

    [project.entry-points."poindexter.media_compositors"]
    ffmpeg_local = "cofounder_agent.services.media_compositors.ffmpeg_local:FFmpegLocalCompositor"

Per-install config lives in
``app_settings.plugin.media_compositor.<name>`` —
binary path, default video / audio codec, target bitrate,
hardware-acceleration toggles, cloud-API credentials, etc.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Literal, Protocol, runtime_checkable


@dataclass
class CompositionScene:
    """One scene the compositor will stitch into the output.

    Attributes:
        clip_path: Local filesystem path to the visual clip MP4
            (or PNG/JPG sequence — composition adapters render
            stills via Ken-Burns themselves).
        narration_path: Local path to the per-scene narration
            audio (typically TTSProvider output). ``None`` skips
            narration on this scene.
        duration_s: Target duration of this scene in the final
            output. The compositor extends or truncates the clip
            to match. ``0`` means "use the clip's native
            duration."
        caption_text: Optional plain text shown as a burned-in
            caption during this scene. Use a CaptionProvider for
            full SRT/VTT timing — this field is for one-off
            overlays.
    """

    clip_path: str
    narration_path: str | None = None
    duration_s: float = 0.0
    caption_text: str = ""


@dataclass
class CompositionRequest:
    """Inputs to one composition job. Compositors read this
    dataclass instead of taking a long kwargs list."""

    scenes: list[CompositionScene]
    soundtrack_path: str | None = None
    caption_track_path: str | None = None
    output_path: str = ""
    width: int = 1920
    height: int = 1080
    fps: int = 30
    codec: str = "h264"
    container: str = "mp4"
    soundtrack_dbfs: float = -18.0
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass
class CompositionResult:
    """Outcome of one composition job.

    Attributes:
        success: True when the compositor wrote a complete output
            file. False on subprocess failure, OOM, codec
            unavailable, etc.
        output_path: Local path the finished file was written to.
            ``None`` on failure.
        duration_s: Actual duration of the output in seconds.
        width, height, fps: Verified physical attributes of the
            output (post-encode), not the requested ones.
        codec: Verified container/codec combination.
        file_size_bytes: Output size on disk.
        error: Human-readable failure summary. ``None`` on
            success.
        cost_usd: Cloud-compositor billing for this job. ``0.0``
            for local compositors.
        electricity_kwh: Local compositors compute this from
            CPU+GPU watts × encode duration. Cloud compositors
            estimate from input duration × a per-codec average.
        metadata: Free-form per-compositor extras (FFmpeg log
            tail, cloud job ID, encoder version, etc.).
    """

    success: bool
    output_path: str | None = None
    duration_s: float = 0.0
    width: int = 0
    height: int = 0
    fps: int = 0
    codec: str = ""
    file_size_bytes: int = 0
    error: str | None = None
    cost_usd: float = 0.0
    electricity_kwh: float = 0.0
    metadata: dict[str, Any] = field(default_factory=dict)


@runtime_checkable
class MediaCompositor(Protocol):
    """Stitch scenes + audio + captions into a finished media file.

    Implementations MUST:

    1. Read their config from ``site_config.get`` /
       ``site_config.get_secret`` under
       ``plugin.media_compositor.<self.name>.*``. Never from env.
    2. Validate every input file exists and is readable BEFORE
       starting the encode — long encodes that fail at minute 9
       because a TTS file was missing are pure waste.
    3. Route every outbound call (cloud upload, cloud render API)
       through the cost-guard with
       ``provider=f"compositor.{self.name}"``.
    4. Set ``is_local`` on cost_guard records correctly: True for
       ffmpeg / local-encoder compositors, False for cloud
       services.

    Attributes:
        name: Compositor name. Matches the entry_point key
            (``"ffmpeg_local"``, ``"mux"``, etc.).
        supports_burned_captions: True when the compositor can
            burn caption text into the visual stream. Compositors
            that only support sidecar SRT/VTT files return False
            and the calling Stage routes captions through a
            separate track instead.
        supported_codecs: Codec identifiers the compositor can
            output (``"h264"``, ``"hevc"``, ``"vp9"``, ``"av1"``).
            Used for capability negotiation when the operator
            requests a codec the default compositor can't handle.
    """

    name: str
    supports_burned_captions: bool
    supported_codecs: tuple[str, ...]

    async def compose(
        self,
        request: CompositionRequest,
        **kwargs: Any,
    ) -> CompositionResult:
        """Render the composition described by ``request``.

        Long-running. Caller awaits to completion; no progress
        callback in V0 (add when a real consumer needs it).

        Compositors MUST NOT raise on a recoverable error —
        return ``success=False`` with ``error`` populated. Raise
        only on programmer error (bad request shape).
        """
        ...

    async def probe(
        self,
        media_path: str,
    ) -> dict[str, Any]:
        """Inspect a media file and return its physical attributes.

        Used by callers that received a media file from another
        Stage and want to know its duration / dimensions before
        deciding what to do with it (e.g., the upload Stage
        rejects a 9:16 short that exceeds 60s).

        Return shape (compositors MUST populate at least the
        first three when the data is available):

            {
                "duration_s": float,
                "width": int,
                "height": int,
                "fps": float,
                "codec": str,
                "container": str,
                "file_size_bytes": int,
                ...
            }
        """
        ...

    def supports(
        self,
        *,
        codec: str,
        container: Literal["mp4", "webm", "mov", "mkv"],
    ) -> bool:
        """Capability check used by the dispatch layer when
        multiple compositors are registered.

        Default implementation should compare ``codec`` against
        :attr:`supported_codecs` and accept a small documented
        set of containers.
        """
        ...
