"""FFmpegLocalCompositor — local ffmpeg subprocess MediaCompositor.

The default compositor for the video pipeline. Stitches per-scene
clips, narration, optional soundtrack, and optional sidecar captions
into a single MP4 by chaining a small number of ffmpeg invocations:

1. **Normalize each scene** — one ffmpeg pass per scene, scaling /
   padding the clip to the requested width × height at the requested
   fps, trimming or looping to ``duration_s``, and muxing the per-scene
   narration into a uniform AAC track. Producing identically-encoded
   intermediates is what lets step 2 stream-copy.
2. **Concat** — single ffmpeg pass with the concat demuxer
   (``-f concat -safe 0``) and ``-c copy``. No re-encode, so this step
   is fast even on hour-long outputs.
3. **Soundtrack mix** *(optional)* — when ``soundtrack_path`` is set,
   ffmpeg ``amix`` blends it under the narration at
   ``soundtrack_dbfs``.
4. **Burn captions** *(optional)* — when ``caption_track_path`` is
   set, ffmpeg's ``subtitles`` filter renders an .srt/.vtt as a
   visual overlay.

Each pass is its own subprocess so failures land in a known step and
the caller gets a precise error string back instead of a 1000-line
ffmpeg log dump.

Config (``plugin.media_compositor.ffmpeg_local`` in app_settings):

- ``enabled`` (bool, default True) — kill switch.
- ``binary_path`` (str, default ``"ffmpeg"``) — path or PATH-name.
- ``ffprobe_path`` (str, default ``"ffprobe"``) — same.
- ``hwaccel`` (str, default ``""``) — when set (e.g. ``"cuda"``,
  ``"qsv"``, ``"vaapi"``), passed to ``-hwaccel`` on every encode.
- ``preset`` (str, default ``"medium"``) — ``-preset`` for libx264 /
  libx265. Use ``"veryfast"`` for short-form, ``"slow"`` for long-form
  if you have headroom.
- ``crf`` (int, default 20) — ``-crf`` quality target. Lower is
  better quality and bigger files.
- ``audio_bitrate`` (str, default ``"192k"``) — AAC bitrate for the
  normalized intermediates and final mux.
- ``loglevel`` (str, default ``"error"``) — ``-loglevel``. Bump to
  ``"warning"`` or ``"info"`` for debugging.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import shutil
import subprocess  # noqa: S404 — local ffmpeg, argv-list call, no shell
import tempfile
import time
from typing import Any, Literal

from plugins.media_compositor import (
    CompositionRequest,
    CompositionResult,
    CompositionScene,
)
from services.cost_guard import CostGuard

logger = logging.getLogger(__name__)


_DEFAULT_BINARY = "ffmpeg"
_DEFAULT_PROBE = "ffprobe"
_DEFAULT_PRESET = "medium"
_DEFAULT_CRF = 20
_DEFAULT_AUDIO_BITRATE = "192k"
_DEFAULT_LOGLEVEL = "error"

# Container → codec compatibility. Used by ``supports()`` so the
# dispatch layer can refuse impossible combinations before we hand
# them to ffmpeg. Conservative; expand when a real consumer needs
# something more exotic.
_CONTAINER_CODECS: dict[str, frozenset[str]] = {
    "mp4": frozenset({"h264", "hevc", "av1"}),
    "mov": frozenset({"h264", "hevc", "prores"}),
    "mkv": frozenset({"h264", "hevc", "av1", "vp9"}),
    "webm": frozenset({"vp9", "av1"}),
}

# Map our short codec names to the libx-* encoder name ffmpeg expects.
_ENCODER_FOR_CODEC: dict[str, str] = {
    "h264": "libx264",
    "hevc": "libx265",
    "av1": "libsvtav1",
    "vp9": "libvpx-vp9",
    "prores": "prores_ks",
}


def _resolve_binary(configured: str, default: str) -> str | None:
    """Locate an absolute path for ``configured`` or ``default``.

    Accepts an absolute path verbatim. Otherwise tries the configured
    PATH-name first, then the canonical default — handles operators
    who shipped with non-default ffmpeg builds.
    """
    if configured and os.path.isabs(configured):
        return configured if os.path.exists(configured) else None
    for candidate in (configured, default):
        if not candidate:
            continue
        located = shutil.which(candidate)
        if located:
            return located
    return None


def _validate_inputs(request: CompositionRequest) -> str | None:
    """Return an error string if any input is unusable, else ``None``.

    Discipline #2: validate everything BEFORE the long encode. A
    composition that fails at minute 9 because a TTS file was missing
    is pure waste.
    """
    if not request.scenes:
        return "request.scenes is empty — nothing to compose"
    if not request.output_path:
        return "request.output_path is required"
    for idx, scene in enumerate(request.scenes):
        if not scene.clip_path or not os.path.exists(scene.clip_path):
            return f"scene[{idx}].clip_path missing or unreadable: {scene.clip_path!r}"
        if scene.narration_path and not os.path.exists(scene.narration_path):
            return (
                f"scene[{idx}].narration_path missing: {scene.narration_path!r}"
            )
    if request.soundtrack_path and not os.path.exists(request.soundtrack_path):
        return f"soundtrack_path missing: {request.soundtrack_path!r}"
    if request.caption_track_path and not os.path.exists(
        request.caption_track_path,
    ):
        return f"caption_track_path missing: {request.caption_track_path!r}"
    if request.codec not in _ENCODER_FOR_CODEC:
        return (
            f"codec={request.codec!r} not supported by ffmpeg_local "
            f"(supported: {sorted(_ENCODER_FOR_CODEC)})"
        )
    return None


def _build_normalize_cmd(
    *,
    binary: str,
    scene: CompositionScene,
    output_path: str,
    width: int,
    height: int,
    fps: int,
    encoder: str,
    preset: str,
    crf: int,
    audio_bitrate: str,
    loglevel: str,
    hwaccel: str,
) -> list[str]:
    """Build the per-scene normalization argv.

    Pads/scales to ``width × height`` (preserving aspect via
    ``force_original_aspect_ratio=decrease`` + black bars), pins fps,
    encodes video with ``encoder``, encodes audio as AAC, trims to
    ``scene.duration_s`` if non-zero. Stills become videos via
    ``-loop 1`` semantics.

    A scene with no narration gets silent AAC of its own duration so
    every intermediate has a uniform A/V track (concat demuxer
    requires this).
    """
    cmd: list[str] = [binary, "-loglevel", loglevel, "-y"]
    if hwaccel:
        cmd.extend(["-hwaccel", hwaccel])

    # Treat any clip ffmpeg can decode uniformly — if it's a still,
    # ffmpeg picks up that stream is single-frame and the scale+fps
    # filters expand it into a video at the requested fps.
    cmd.extend(["-i", scene.clip_path])

    # Audio source: narration if provided, else generated silence.
    if scene.narration_path:
        cmd.extend(["-i", scene.narration_path])
    else:
        cmd.extend(["-f", "lavfi", "-i", "anullsrc=r=48000:cl=stereo"])

    # Video filtergraph — scale to fit, pad to fill, normalize fps.
    vf = (
        f"scale=w={width}:h={height}:force_original_aspect_ratio=decrease,"
        f"pad=w={width}:h={height}:x=(ow-iw)/2:y=(oh-ih)/2,"
        f"fps={fps}"
    )
    cmd.extend(["-vf", vf])

    # Duration handling. duration_s=0 means "use the clip's native
    # duration"; otherwise pin to the requested value.
    if scene.duration_s and scene.duration_s > 0:
        cmd.extend(["-t", f"{scene.duration_s:.3f}"])

    # Map streams so the AAC track always wins even if the input clip
    # has its own audio (we don't want lipsync surprises).
    cmd.extend([
        "-map", "0:v:0",
        "-map", "1:a:0",
        "-c:v", encoder,
        "-pix_fmt", "yuv420p",
        "-preset", preset,
        "-crf", str(crf),
        "-c:a", "aac",
        "-b:a", audio_bitrate,
        "-ar", "48000",
        "-shortest",
        output_path,
    ])
    return cmd


def _build_concat_cmd(
    *,
    binary: str,
    list_path: str,
    output_path: str,
    loglevel: str,
) -> list[str]:
    """Stream-copy concat over a list file.

    Only works when every input was encoded identically — which the
    normalize step guarantees. ``-safe 0`` allows absolute paths in
    the list file.
    """
    return [
        binary, "-loglevel", loglevel, "-y",
        "-f", "concat",
        "-safe", "0",
        "-i", list_path,
        "-c", "copy",
        output_path,
    ]


def _build_soundtrack_mix_cmd(
    *,
    binary: str,
    video_in: str,
    soundtrack_path: str,
    soundtrack_dbfs: float,
    encoder: str,
    preset: str,
    crf: int,
    audio_bitrate: str,
    loglevel: str,
    hwaccel: str,
) -> list[str]:
    """Mix ``soundtrack_path`` under the existing audio at the requested
    dBFS, re-muxing into a fresh MP4.

    Re-encodes video too (libx264 over a stream-copy pass would be
    cheaper, but mixing audio without a re-encode is fragile across
    ffmpeg versions when the source has a non-standard atom order).
    """
    cmd: list[str] = [binary, "-loglevel", loglevel, "-y"]
    if hwaccel:
        cmd.extend(["-hwaccel", hwaccel])

    # First input: composed video; second: soundtrack.
    cmd.extend(["-i", video_in, "-i", soundtrack_path])

    # ``volume`` in dB is exactly what the operator wants. Then amix
    # blends. ``duration=first`` means the music follows the video,
    # not the other way around.
    af = (
        f"[1:a]volume={soundtrack_dbfs}dB[bg];"
        f"[0:a][bg]amix=inputs=2:duration=first:dropout_transition=2[a]"
    )
    cmd.extend([
        "-filter_complex", af,
        "-map", "0:v:0",
        "-map", "[a]",
        "-c:v", encoder,
        "-pix_fmt", "yuv420p",
        "-preset", preset,
        "-crf", str(crf),
        "-c:a", "aac",
        "-b:a", audio_bitrate,
    ])
    return cmd


def _build_burn_captions_cmd(
    *,
    binary: str,
    video_in: str,
    caption_path: str,
    encoder: str,
    preset: str,
    crf: int,
    loglevel: str,
    hwaccel: str,
    output_path: str,
) -> list[str]:
    """Burn an .srt/.vtt sidecar into the visual stream.

    Uses ffmpeg's ``subtitles`` filter — re-encodes video, copies the
    audio. Caption file path is escaped: backslashes and colons inside
    the filter graph need doubling, which is why we substitute via
    %s/%c rather than f-string interpolation of an unsanitized path.
    """
    # ``subtitles`` filter requires forward slashes and backslash-escaped
    # colons even on Windows — and the path itself goes inside a
    # filtergraph string, so single quotes wrapping it disable the
    # outer comma parser.
    safe = caption_path.replace("\\", "/").replace(":", r"\:")
    cmd: list[str] = [binary, "-loglevel", loglevel, "-y"]
    if hwaccel:
        cmd.extend(["-hwaccel", hwaccel])
    cmd.extend([
        "-i", video_in,
        "-vf", f"subtitles='{safe}'",
        "-c:v", encoder,
        "-pix_fmt", "yuv420p",
        "-preset", preset,
        "-crf", str(crf),
        "-c:a", "copy",
        output_path,
    ])
    return cmd


def _run_blocking(cmd: list[str]) -> tuple[int, str, str]:
    """Synchronous subprocess wrapper, separated for ``to_thread`` use.

    Captures stdout + stderr without flooding the parent's logging
    pipeline. Test code monkeypatches this function to avoid spawning
    real ffmpeg processes.
    """
    proc = subprocess.run(  # noqa: S603 — argv list, no shell
        cmd,
        capture_output=True,
        text=True,
        check=False,
    )
    return proc.returncode, proc.stdout or "", proc.stderr or ""


def _ffprobe_blocking(probe: str, media_path: str) -> tuple[int, str, str]:
    """Run ``ffprobe`` and return its JSON payload as a string."""
    proc = subprocess.run(  # noqa: S603 — argv list, no shell
        [
            probe,
            "-v", "quiet",
            "-print_format", "json",
            "-show_format",
            "-show_streams",
            media_path,
        ],
        capture_output=True,
        text=True,
        check=False,
    )
    return proc.returncode, proc.stdout or "", proc.stderr or ""


def _parse_probe(payload: str) -> dict[str, Any]:
    """Translate ffprobe JSON into the Protocol's flat dict shape.

    ffprobe gives us streams (video, audio, subtitle, …) and a top-
    level ``format`` block. We pull the first video stream's
    dimensions/fps/codec and the format's duration/size/container.
    """
    try:
        data = json.loads(payload)
    except (ValueError, TypeError):
        return {}

    out: dict[str, Any] = {}
    fmt = data.get("format") or {}
    if "duration" in fmt:
        try:
            out["duration_s"] = float(fmt["duration"])
        except (TypeError, ValueError):
            pass
    if "size" in fmt:
        try:
            out["file_size_bytes"] = int(fmt["size"])
        except (TypeError, ValueError):
            pass
    if "format_name" in fmt:
        # ffprobe returns comma-joined names ("mov,mp4,m4a,..."); take
        # the first one as a best-effort container label.
        out["container"] = str(fmt["format_name"]).split(",")[0]

    for stream in data.get("streams") or []:
        if stream.get("codec_type") == "video":
            if "width" in stream:
                out["width"] = int(stream["width"])
            if "height" in stream:
                out["height"] = int(stream["height"])
            codec_name = stream.get("codec_name")
            if codec_name:
                out["codec"] = str(codec_name)
            # ffprobe r_frame_rate is a fraction like "30000/1001".
            r_fr = stream.get("r_frame_rate") or ""
            if "/" in r_fr:
                num, den = r_fr.split("/", 1)
                try:
                    out["fps"] = float(num) / float(den) if float(den) else 0.0
                except (TypeError, ValueError, ZeroDivisionError):
                    pass
            break
    return out


class FFmpegLocalCompositor:
    """ffmpeg-driven local MediaCompositor.

    Runs every encode on the host CPU/GPU. ``is_local=True`` on the
    cost-guard record. Designed to be the V0 default — boring,
    inspectable, no cloud dependency.
    """

    name = "ffmpeg_local"
    supports_burned_captions = True
    supported_codecs: tuple[str, ...] = ("h264", "hevc", "av1", "vp9", "prores")

    def __init__(self, site_config: Any = None) -> None:
        self._site_config = site_config

    def _get(self, key: str, default: Any) -> Any:
        if self._site_config is None:
            return default
        return self._site_config.get(
            f"plugin.media_compositor.ffmpeg_local.{key}",
            default,
        )

    def _build_cost_guard(self, kwargs: dict[str, Any]) -> CostGuard:
        injected = kwargs.get("_cost_guard")
        if isinstance(injected, CostGuard):
            return injected
        site_config = kwargs.get("_site_config", self._site_config)
        pool = kwargs.get("_pool")
        if pool is None and site_config is not None:
            pool = getattr(site_config, "_pool", None)
        return CostGuard(site_config=site_config, pool=pool)

    def supports(
        self,
        *,
        codec: str,
        container: Literal["mp4", "webm", "mov", "mkv"],
    ) -> bool:
        allowed = _CONTAINER_CODECS.get(container)
        if allowed is None:
            return False
        return codec in allowed and codec in self.supported_codecs

    async def compose(
        self,
        request: CompositionRequest,
        **kwargs: Any,
    ) -> CompositionResult:
        if not bool(self._get("enabled", True)):
            return CompositionResult(
                success=False,
                error="FFmpegLocalCompositor disabled in app_settings",
            )

        # Cheap validation BEFORE we spawn anything.
        validation_error = _validate_inputs(request)
        if validation_error:
            return CompositionResult(success=False, error=validation_error)

        configured_binary = str(self._get("binary_path", _DEFAULT_BINARY) or _DEFAULT_BINARY)
        binary = _resolve_binary(configured_binary, _DEFAULT_BINARY)
        if binary is None:
            return CompositionResult(
                success=False,
                error=(
                    f"ffmpeg binary not found (configured={configured_binary!r}). "
                    "Install ffmpeg and set "
                    "plugin.media_compositor.ffmpeg_local.binary_path."
                ),
            )

        configured_probe = str(self._get("ffprobe_path", _DEFAULT_PROBE) or _DEFAULT_PROBE)
        probe = _resolve_binary(configured_probe, _DEFAULT_PROBE)
        # Probe is optional — the compose result tolerates a missing
        # ffprobe by leaving the verified-attrs fields at zero.

        preset = str(self._get("preset", _DEFAULT_PRESET) or _DEFAULT_PRESET)
        crf = int(self._get("crf", _DEFAULT_CRF) or _DEFAULT_CRF)
        audio_bitrate = str(self._get("audio_bitrate", _DEFAULT_AUDIO_BITRATE) or _DEFAULT_AUDIO_BITRATE)
        loglevel = str(self._get("loglevel", _DEFAULT_LOGLEVEL) or _DEFAULT_LOGLEVEL)
        hwaccel = str(self._get("hwaccel", "") or "")
        encoder = _ENCODER_FOR_CODEC[request.codec]

        cost_guard = self._build_cost_guard(kwargs)
        started = time.perf_counter()
        success = True
        error: str | None = None
        ffmpeg_log_tail = ""

        # Output dir must exist before ffmpeg writes.
        os.makedirs(os.path.dirname(os.path.abspath(request.output_path)) or ".", exist_ok=True)

        with tempfile.TemporaryDirectory(prefix="poindexter_compose_") as tmpdir:
            try:
                # 1. Normalize each scene.
                normalized_paths: list[str] = []
                for idx, scene in enumerate(request.scenes):
                    norm_path = os.path.join(tmpdir, f"scene_{idx:04d}.mp4")
                    cmd = _build_normalize_cmd(
                        binary=binary,
                        scene=scene,
                        output_path=norm_path,
                        width=request.width,
                        height=request.height,
                        fps=request.fps,
                        encoder=encoder,
                        preset=preset,
                        crf=crf,
                        audio_bitrate=audio_bitrate,
                        loglevel=loglevel,
                        hwaccel=hwaccel,
                    )
                    rc, _, err = await asyncio.to_thread(_run_blocking, cmd)
                    if rc != 0:
                        success = False
                        ffmpeg_log_tail = err.strip()[-1000:]
                        error = (
                            f"normalize step failed on scene[{idx}] "
                            f"(rc={rc}): {ffmpeg_log_tail or '(no stderr)'}"
                        )
                        break
                    normalized_paths.append(norm_path)

                # 2. Concat (only if normalize succeeded for all scenes).
                concat_path = os.path.join(tmpdir, "concat.mp4")
                if success:
                    list_path = os.path.join(tmpdir, "concat_list.txt")
                    with open(list_path, "w", encoding="utf-8") as f:
                        for p in normalized_paths:
                            # concat demuxer escapes single quotes in
                            # paths — safe to assume tempdir paths
                            # don't include them here.
                            f.write(f"file '{p}'\n")
                    cmd = _build_concat_cmd(
                        binary=binary,
                        list_path=list_path,
                        output_path=concat_path,
                        loglevel=loglevel,
                    )
                    rc, _, err = await asyncio.to_thread(_run_blocking, cmd)
                    if rc != 0:
                        success = False
                        ffmpeg_log_tail = err.strip()[-1000:]
                        error = f"concat step failed (rc={rc}): {ffmpeg_log_tail or '(no stderr)'}"

                # 3. Soundtrack mix (optional).
                stage_in = concat_path
                if success and request.soundtrack_path:
                    mixed_path = os.path.join(tmpdir, "mixed.mp4")
                    cmd = _build_soundtrack_mix_cmd(
                        binary=binary,
                        video_in=stage_in,
                        soundtrack_path=request.soundtrack_path,
                        soundtrack_dbfs=request.soundtrack_dbfs,
                        encoder=encoder,
                        preset=preset,
                        crf=crf,
                        audio_bitrate=audio_bitrate,
                        loglevel=loglevel,
                        hwaccel=hwaccel,
                    ) + [mixed_path]
                    rc, _, err = await asyncio.to_thread(_run_blocking, cmd)
                    if rc != 0:
                        success = False
                        ffmpeg_log_tail = err.strip()[-1000:]
                        error = f"soundtrack mix failed (rc={rc}): {ffmpeg_log_tail or '(no stderr)'}"
                    else:
                        stage_in = mixed_path

                # 4. Burn captions (optional). When no caption track is
                # provided this is a no-op and we copy the previous
                # stage's file to the requested output path.
                if success:
                    if request.caption_track_path:
                        cmd = _build_burn_captions_cmd(
                            binary=binary,
                            video_in=stage_in,
                            caption_path=request.caption_track_path,
                            encoder=encoder,
                            preset=preset,
                            crf=crf,
                            loglevel=loglevel,
                            hwaccel=hwaccel,
                            output_path=request.output_path,
                        )
                        rc, _, err = await asyncio.to_thread(_run_blocking, cmd)
                        if rc != 0:
                            success = False
                            ffmpeg_log_tail = err.strip()[-1000:]
                            error = (
                                f"caption burn failed (rc={rc}): "
                                f"{ffmpeg_log_tail or '(no stderr)'}"
                            )
                    else:
                        shutil.copyfile(stage_in, request.output_path)

            except Exception as exc:
                success = False
                error = f"{type(exc).__name__}: {exc}"
                logger.exception("[ffmpeg_local] compose raised")

            duration_ms = int((time.perf_counter() - started) * 1000)

        # Verify the output by probing it (best-effort).
        probed: dict[str, Any] = {}
        if success and probe and os.path.exists(request.output_path):
            try:
                rc, payload, _ = await asyncio.to_thread(
                    _ffprobe_blocking, probe, request.output_path,
                )
                if rc == 0:
                    probed = _parse_probe(payload)
            except Exception as exc:
                logger.warning("[ffmpeg_local] ffprobe failed: %s", exc)

        # Cost-guard: local provider, electricity-only.
        try:
            await cost_guard.record_usage(
                provider=f"compositor.{self.name}",
                model=request.codec,
                prompt_tokens=0,
                completion_tokens=0,
                phase=str(kwargs.get("phase", "compose")),
                task_id=kwargs.get("task_id"),
                success=success,
                duration_ms=duration_ms,
                is_local=True,
            )
        except Exception as exc:
            logger.warning("[ffmpeg_local] cost recording failed: %s", exc)

        file_size = 0
        if success and os.path.exists(request.output_path):
            try:
                file_size = os.path.getsize(request.output_path)
            except OSError:
                pass

        return CompositionResult(
            success=success,
            output_path=request.output_path if success else None,
            duration_s=float(probed.get("duration_s", 0.0)),
            width=int(probed.get("width", 0)),
            height=int(probed.get("height", 0)),
            fps=int(probed.get("fps", 0) or 0),
            codec=str(probed.get("codec", "") or ""),
            file_size_bytes=file_size,
            error=error,
            cost_usd=0.0,
            electricity_kwh=cost_guard.estimate_local_kwh(duration_ms=duration_ms),
            metadata={
                "binary": binary,
                "encoder": encoder,
                "preset": preset,
                "crf": crf,
                "hwaccel": hwaccel,
                "duration_ms": duration_ms,
                "scene_count": len(request.scenes),
                "ffmpeg_log_tail": ffmpeg_log_tail,
            },
        )

    async def probe(
        self,
        media_path: str,
    ) -> dict[str, Any]:
        if not media_path or not os.path.exists(media_path):
            return {}

        configured_probe = str(self._get("ffprobe_path", _DEFAULT_PROBE) or _DEFAULT_PROBE)
        binary = _resolve_binary(configured_probe, _DEFAULT_PROBE)
        if binary is None:
            return {}

        try:
            rc, payload, _ = await asyncio.to_thread(
                _ffprobe_blocking, binary, media_path,
            )
        except Exception as exc:
            logger.warning("[ffmpeg_local] probe raised: %s", exc)
            return {}
        if rc != 0:
            return {}

        out = _parse_probe(payload)
        try:
            out["file_size_bytes"] = os.path.getsize(media_path)
        except OSError:
            pass
        return out
