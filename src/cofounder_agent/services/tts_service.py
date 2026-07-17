"""Podcast TTS dispatcher — synthesizes speech via Speaches.

Thin wrapper around the Speaches OpenAI-compatible /v1/audio/speech
endpoint. Speaches runs as the poindexter-speaches Docker container
(default port 8001 on the host, :8000 inside the compose network).

This service is specifically for **podcast narration** — converting the
LLM-generated podcast script into a spoken audio file. It is intentionally
separate from audio_gen_service (which handles music/SFX generation via
StableAudioOpen) because speech synthesis and music generation have
different APIs, prompts, and return contracts.

Configuration (all in app_settings, default-off):
- podcast_tts_enabled       false  — flip to true to activate
- podcast_tts_base_url      http://speaches:8000/v1  — compose URL
- podcast_tts_voice         bf_emma  — Kokoro voice id
- podcast_tts_model         speaches-ai/Kokoro-82M-v1.0-ONNX
- podcast_tts_format        mp3  — output format. MUST be a self-
  synchronizing format (mp3/opus/aac), NOT wav: Speaches splits long
  input into segments internally and byte-concatenates them. A
  multi-segment WAV keeps only the FIRST segment's RIFF header, so
  players stop at ~24s and it is unrecoverable. Concatenated MP3 frames
  are all present, but the byte-concat leaves N embedded per-segment
  Xing/LAME headers and only the first segment's duration in the stream
  header — so we normalize the stream with ffmpeg before returning (see
  ``_remux_concatenated_audio``). Without it, players honor the short
  header and cut off mid-episode, and strict transcoders can mishandle
  the multi-header structure at the tail.
- podcast_tts_remux_enabled true  — flip false to skip normalization
  (e.g. a TTS provider without the concat bug, or hosts with no
  ffmpeg). Fail-soft regardless.
- podcast_tts_remux_mode   reencode — 'reencode' (default) decodes and
  re-encodes ONCE to a single clean stream, collapsing the per-segment
  headers (the robust fix); 'copy' is the legacy lossless `-c copy`
  that only rewrites the duration header.
- podcast_tts_remux_bitrate 192k — output bitrate for re-encode mode
  (effectively transparent for spoken word; a lower bitrate here compounds
  with the TTS engine's own encode into an audible double lossy transcode).
- podcast_tts_loudnorm_enabled true — EBU R128 loudness normalization
  (the audio_clipping fix). Kokoro emits full-scale audio (peak ~0.0
  dBFS) that trips the qa.audio -0.1 dBFS clip gate and risks true-peak
  distortion after MP3 encode. ffmpeg ``loudnorm`` caps the true peak
  (headroom) and hits the loudness target. Rides the remux re-encode
  (one pass) and ALSO runs when remux is off. Fail-soft; needs ffmpeg.
- podcast_tts_loudnorm_i   -16   — integrated loudness LUFS target
  (the Apple/Spotify podcast standard).
- podcast_tts_loudnorm_tp  -1.5  — max true peak dBTP (the headroom
  that pulls max_volume below the clip gate).
- podcast_tts_loudnorm_lra 11    — EBU R128 loudness range.
- podcast_tts_loudnorm_ar  44100 — resample target (loudnorm upsamples
  to 192 kHz internally; resample back to a distribution rate).

Never raises — callers in generate_media_scripts treat audio as
best-effort; a missing Speaches container is logged, not fatal. The
remux is fail-soft too: any ffmpeg error returns the raw Speaches bytes.
"""

from __future__ import annotations

import asyncio
import logging
import os
import shutil
import tempfile
from typing import Any

import httpx

logger = logging.getLogger(__name__)

_DEFAULT_BASE_URL = "http://speaches:8000/v1"
_DEFAULT_VOICE = "bf_emma"
_DEFAULT_MODEL = "speaches-ai/Kokoro-82M-v1.0-ONNX"
# mp3, NOT wav: Speaches byte-concatenates per-segment audio for long input.
# A concatenated WAV keeps only the first segment's RIFF header (~24s, and
# unrecoverable); concatenated MP3 frames are all present but the stream's
# duration header still reports only segment 1 — repaired by the ffmpeg
# `-c copy` remux in synthesize_speech (see _remux_concatenated_audio).
_DEFAULT_FORMAT = "mp3"
_HTTP_TIMEOUT = httpx.Timeout(120.0, connect=5.0)

# Formats we normalize after Speaches byte-concatenates its internal segments
# (see module docstring). Self-synchronizing / streamable containers only — a
# concatenated WAV is NOT listed because ffmpeg would read just the first RIFF
# chunk and truncate.
_REMUX_FORMATS = frozenset({"mp3", "aac", "opus"})

# How to normalize the byte-concatenated stream:
#   'reencode' (default) — decode all segments and re-encode ONCE to a single
#     clean stream, collapsing the per-segment Xing/LAME headers that players /
#     podcast transcoders can mishandle at segment + tail boundaries.
#   'copy' — legacy lossless `-c copy`: rewrites the whole-file duration header
#     but LEAVES the embedded per-segment headers in place.
# DB-tunable via podcast_tts_remux_mode / podcast_tts_remux_bitrate. 192k (not
# 96k) is the delivery bitrate for spoken word — effectively transparent; 96k
# was compounding with the TTS sidecar's own lossy encode into an audible
# double transcode (the audio-fidelity investigation).
_DEFAULT_REMUX_MODE = "reencode"
_DEFAULT_REMUX_BITRATE = "192k"
# Per-format encoder used in re-encode mode (libmp3lame is universally built in).
_REENCODE_CODEC = {"mp3": "libmp3lame", "aac": "aac", "opus": "libopus"}

# EBU R128 loudness normalization (audio_clipping fix). Kokoro outputs full-scale
# audio (peak ~0.0 dBFS); ffmpeg loudnorm pulls integrated loudness to the podcast
# target AND caps the true peak so max_volume drops below the qa.audio -0.1 dBFS
# clip gate. DB-tunable via podcast_tts_loudnorm_*.
_DEFAULT_LOUDNORM_I = "-16"      # integrated loudness LUFS (Apple/Spotify target)
_DEFAULT_LOUDNORM_TP = "-1.5"    # max true peak dBTP (the headroom)
_DEFAULT_LOUDNORM_LRA = "11"     # loudness range
_DEFAULT_LOUDNORM_AR = "44100"   # resample target (loudnorm upsamples to 192 kHz)


def is_tts_enabled(site_config: Any) -> bool:
    """Return True iff podcast_tts_enabled is set to a truthy value."""
    if site_config is None:
        return False
    try:
        return bool(site_config.get_bool("podcast_tts_enabled", False))
    except Exception:
        pass
    try:
        v = site_config.get("podcast_tts_enabled", "")
        return str(v).strip().lower() in ("true", "1", "yes", "on")
    except Exception as exc:
        logger.warning(
            "[tts_service] is_tts_enabled failed to read setting: %s — "
            "treating TTS as disabled",
            exc,
        )
        return False


def resolve_tts_format(site_config: Any) -> str:
    """Resolve the TTS output format. THE single seam — do not inline a default.

    Every caller that needs the format (the synth call itself, and the pipeline
    stage naming the temp file) must come through here, so the file extension
    and the actual audio bytes cannot disagree.

    They did disagree: this stage defaulted to ``wav`` while the synth call
    defaulted to ``mp3``, so an unconfigured install wrote mp3 bytes into a
    ``.wav`` file. And ``wav`` is not merely a different choice — it is the
    *unrecoverable* Speaches failure (#1696/#1706): only the first segment's
    RIFF header survives the byte-concatenation, and ``ffmpeg -c copy`` remux
    reads that first chunk and truncates. Never default to it.
    """
    fmt = _resolve(site_config, "podcast_tts_format", _DEFAULT_FORMAT)
    # '' is the app_settings unset sentinel (feedback_app_settings_value_not_null),
    # so an empty value means "unset", not "no extension".
    return (fmt or _DEFAULT_FORMAT).strip().lower() or _DEFAULT_FORMAT


def _resolve(site_config: Any, key: str, default: str) -> str:
    """Read a string setting, fall back to default on any error."""
    if site_config is None:
        return default
    try:
        v = site_config.get(key, default)
        return str(v or default).strip() or default
    except Exception:
        return default


def _resolve_bool(site_config: Any, key: str, default: bool) -> bool:
    """Read a boolean setting, preferring get_bool and falling back to a string
    parse for configs that only expose get(). Logs (not silent) on failure."""
    if site_config is None:
        return default
    try:
        getter = getattr(site_config, "get_bool", None)
        if getter is not None:
            return bool(getter(key, default))
        v = site_config.get(key, "")
        s = str(v).strip().lower()
        return default if not s else s in ("true", "1", "yes", "on")
    except Exception as exc:
        logger.warning(
            "[tts_service] failed to read bool setting %s (%s) — using default %s",
            key, exc, default,
        )
        return default


def _resolve_numeric_str(site_config: Any, key: str, default: str) -> str:
    """Read a numeric setting as a string, validating it parses as a float.

    The loudnorm filter values (LUFS / dBTP / sample rate) are interpolated
    straight into the ffmpeg ``-af`` argument, so a non-numeric operator value
    would otherwise make ffmpeg fail and fall back to raw (clipping) audio with
    no clear cause. We validate here and fall back to the known-good default with
    a warning, keeping the string form ('-16' not '-16.0') so the filter reads
    cleanly.
    """
    raw = _resolve(site_config, key, default)
    try:
        float(raw)
        return raw
    except (TypeError, ValueError):
        logger.warning(
            "[tts_service] %s=%r is not numeric — using default %s",
            key, raw, default,
        )
        return default


async def render_openai_tts(
    *,
    base_url: str,
    model: str,
    voice: str,
    text: str,
    response_format: str = _DEFAULT_FORMAT,
    api_key: str = "speaches",
    extra_body: dict[str, Any] | None = None,
    read_timeout: float | None = None,
    remux_enabled: bool = True,
    remux_mode: str = _DEFAULT_REMUX_MODE,
    remux_bitrate: str = _DEFAULT_REMUX_BITRATE,
    loudnorm_enabled: bool = True,
    loudnorm_i: str = _DEFAULT_LOUDNORM_I,
    loudnorm_tp: str = _DEFAULT_LOUDNORM_TP,
    loudnorm_lra: str = _DEFAULT_LOUDNORM_LRA,
    loudnorm_ar: str = _DEFAULT_LOUDNORM_AR,
    encode_format: str | None = None,
) -> bytes | None:
    """POST to an OpenAI-compatible /audio/speech endpoint and normalize.

    Engine-agnostic core shared by ``synthesize_speech`` (Speaches) and the
    bake-off TTSProvider plugins. Returns normalized audio bytes, or ``None``
    on any transport/HTTP error (never raises — callers fall back).

    ``extra_body`` is merged into the JSON request for non-standard engine
    knobs (Chatterbox ``exaggeration`` / ``cfg_weight``). The remux + EBU-R128
    loudnorm pass is the same one the Speaches path uses (see
    ``_remux_concatenated_audio``).

    ``read_timeout`` overrides the 120s default read timeout — the bake-off
    sidecars can run on CPU (no spare VRAM), where synthesizing a full
    paragraph legitimately takes minutes, so the provider passes a generous
    per-engine ``timeout_s``. Speaches (fast, GPU) keeps the default.

    ``encode_format`` names the DELIVERY format wanted back when
    ``response_format`` is ``"wav"`` — for an engine whose sidecar already
    concatenates raw samples before its own single encode (Chatterbox), the
    wav response is always one valid file, so requesting it lossless and
    encoding to ``encode_format`` here is exactly one lossy pass, instead of
    decoding an already-lossy mp3/aac/opus response and re-encoding it again.
    Ignored when ``response_format`` isn't ``"wav"``.
    """
    base_url = (base_url or "").rstrip("/")
    fmt = (response_format or _DEFAULT_FORMAT).lower()
    body: dict[str, Any] = {
        "model": model,
        "voice": voice,
        "input": text,
        "response_format": fmt,
    }
    if extra_body:
        body.update(extra_body)

    timeout = (
        _HTTP_TIMEOUT if read_timeout is None
        else httpx.Timeout(read_timeout, connect=5.0)
    )
    try:
        async with httpx.AsyncClient(timeout=timeout) as client:
            resp = await client.post(
                f"{base_url}/audio/speech",
                headers={"Authorization": f"Bearer {api_key}"},
                json=body,
            )
    except Exception as exc:
        logger.warning(
            "[tts_service] TTS endpoint unreachable at %s: %s", base_url, exc,
        )
        return None

    if resp.status_code != 200:
        logger.warning(
            "[tts_service] TTS endpoint returned %d: %s",
            resp.status_code, (getattr(resp, "text", "") or "")[:200],
        )
        return None

    audio_bytes = resp.content
    if not audio_bytes:
        logger.warning("[tts_service] TTS endpoint returned empty body")
        return None

    # encode_format forces the call even with remux/loudnorm both off: for a
    # wav wire-format response it's the only step that produces the delivery
    # format at all (there's no concatenation-header problem to "normalize"
    # away, unlike the Speaches mp3/aac/opus path).
    if remux_enabled or loudnorm_enabled or encode_format is not None:
        audio_bytes = await _remux_concatenated_audio(
            audio_bytes, fmt, mode=remux_mode, bitrate=remux_bitrate,
            loudnorm=loudnorm_enabled,
            loudnorm_i=loudnorm_i, loudnorm_tp=loudnorm_tp,
            loudnorm_lra=loudnorm_lra, loudnorm_ar=loudnorm_ar,
            encode_format=encode_format,
        )
    return audio_bytes


async def synthesize_speech(
    text: str,
    *,
    site_config: Any,
    output_path: str | None = None,
    voice: str | None = None,
) -> bytes | None:
    """Convert text to speech via Speaches and return the audio bytes.

    Returns ``None`` when TTS is disabled, the container is unreachable,
    or the request fails. Never raises.

    Args:
        text: The text to synthesize (podcast script).
        site_config: SiteConfig instance to read TTS settings from.
        output_path: If provided, also write the bytes to this path
            (convenience for callers that want a file on disk). The
            bytes are still returned regardless.
        voice: Optional voice override. When provided, takes precedence
            over the ``podcast_tts_voice`` app_setting so callers can
            drive per-episode voice rotation without mutating config.
    """
    if not is_tts_enabled(site_config):
        return None

    text = (text or "").strip()
    if not text:
        return None

    base_url = _resolve(site_config, "podcast_tts_base_url", _DEFAULT_BASE_URL)
    voice = voice or _resolve(site_config, "podcast_tts_voice", _DEFAULT_VOICE)
    model = _resolve(site_config, "podcast_tts_model", _DEFAULT_MODEL)
    fmt = resolve_tts_format(site_config)

    # Delegate the OpenAI POST + remux/loudnorm normalization to the shared,
    # engine-agnostic helper (the same one the bake-off TTSProvider plugins use).
    audio_bytes = await render_openai_tts(
        base_url=base_url,
        model=model,
        voice=voice,
        text=text,
        response_format=fmt,
        remux_enabled=_resolve_bool(site_config, "podcast_tts_remux_enabled", True),
        remux_mode=_resolve(site_config, "podcast_tts_remux_mode", _DEFAULT_REMUX_MODE),
        remux_bitrate=_resolve(
            site_config, "podcast_tts_remux_bitrate", _DEFAULT_REMUX_BITRATE
        ),
        loudnorm_enabled=_resolve_bool(
            site_config, "podcast_tts_loudnorm_enabled", True
        ),
        loudnorm_i=_resolve_numeric_str(
            site_config, "podcast_tts_loudnorm_i", _DEFAULT_LOUDNORM_I
        ),
        loudnorm_tp=_resolve_numeric_str(
            site_config, "podcast_tts_loudnorm_tp", _DEFAULT_LOUDNORM_TP
        ),
        loudnorm_lra=_resolve_numeric_str(
            site_config, "podcast_tts_loudnorm_lra", _DEFAULT_LOUDNORM_LRA
        ),
        loudnorm_ar=_resolve_numeric_str(
            site_config, "podcast_tts_loudnorm_ar", _DEFAULT_LOUDNORM_AR
        ),
    )
    if audio_bytes is None:
        return None

    logger.info(
        "[tts_service] TTS synthesized: %d bytes (voice=%s, fmt=%s)",
        len(audio_bytes), voice, fmt,
    )

    if output_path:
        try:
            await asyncio.to_thread(_write_bytes, output_path, audio_bytes)
            logger.info("[tts_service] Wrote podcast audio to %s", output_path)
        except Exception as exc:
            logger.warning("[tts_service] Failed to write %s: %s", output_path, exc)

    return audio_bytes


def _write_bytes(path: str, data: bytes) -> None:
    """Sync helper for asyncio.to_thread — avoid blocking the event loop."""
    with open(path, "wb") as f:
        f.write(data)


def _read_bytes(path: str) -> bytes:
    """Sync helper for asyncio.to_thread — avoid blocking the event loop."""
    with open(path, "rb") as f:
        return f.read()


async def _remux_concatenated_audio(
    audio_bytes: bytes,
    fmt: str,
    *,
    mode: str = _DEFAULT_REMUX_MODE,
    bitrate: str = _DEFAULT_REMUX_BITRATE,
    loudnorm: bool = False,
    loudnorm_i: str = _DEFAULT_LOUDNORM_I,
    loudnorm_tp: str = _DEFAULT_LOUDNORM_TP,
    loudnorm_lra: str = _DEFAULT_LOUDNORM_LRA,
    loudnorm_ar: str = _DEFAULT_LOUDNORM_AR,
    encode_format: str | None = None,
) -> bytes:
    """Normalize Speaches' byte-concatenated multi-segment audio to one stream,
    OR (when ``fmt == "wav"`` and ``encode_format`` is given) encode a verified
    single-file wav straight to the delivery format.

    Speaches splits long input into segments, encodes each to its own MP3, and
    byte-concatenates them — leaving N embedded per-segment Xing/LAME headers
    and only the FIRST segment's duration in the stream header. Two modes repair
    it (selected by ``mode``):

    - ``reencode`` (default): decode every frame and re-encode ONCE to a single
      clean stream at ``bitrate``. This both rewrites a correct whole-file
      duration header AND collapses the embedded per-segment headers — the
      multi-header structure is what some players / podcast transcoders mishandle
      at segment and tail boundaries (a duplicated/garbled ending). One lossy
      pass on spoken-word audio is inaudible.
    - ``copy``: legacy lossless ``-c copy`` — re-reads all frames and writes one
      correct duration header, but LEAVES the embedded per-segment headers in
      place. Kept for back-compat / operators who want zero re-encode.

    When ``loudnorm`` is set, an EBU R128 ``loudnorm=I=..:TP=..:LRA=..`` filter
    (plus an ``aresample`` back off loudnorm's internal 192 kHz) is applied — the
    audio_clipping fix: Kokoro/Chatterbox emit full-scale audio (peak ~0.0 dBFS),
    and capping the true peak to ``loudnorm_tp`` restores the headroom that keeps
    ``max_volume`` below the qa.audio clip gate while hitting the ``loudnorm_i``
    loudness target. A filter graph cannot ride on ``-c copy``, so ``loudnorm``
    forces a re-encode regardless of ``mode``.

    ``fmt == "wav"`` is normally left untouched (a byte-concatenated WAV would
    truncate to its first RIFF chunk, so processing it here would be unsafe
    without knowing it's a single file). Passing ``encode_format`` is the
    caller's attestation that this wav IS a single, verified file (e.g.
    Chatterbox's sidecar, which concatenates raw PCM before its own one encode)
    — in that case this does exactly ONE ffmpeg pass: decode the lossless wav,
    optionally apply loudnorm, and encode straight to ``encode_format`` at
    ``bitrate``. That's one lossy generation total, versus decoding an
    already-lossy mp3/aac/opus response and re-encoding it a second time.

    Fail-soft: returns the original bytes on any error.
    """
    if fmt == "wav" and encode_format is None:
        return audio_bytes
    if fmt != "wav" and fmt not in _REMUX_FORMATS:
        return audio_bytes
    ffmpeg = shutil.which("ffmpeg")
    if not ffmpeg:
        logger.warning(
            "[tts_service] ffmpeg not found — skipping audio normalization; "
            "podcast/narration may report a short duration and cut off. Install "
            "ffmpeg or set podcast_tts_remux_enabled=false to silence this."
        )
        return audio_bytes

    # EBU R128 loudness normalization (audio_clipping fix). loudnorm caps the
    # true peak (headroom below the qa.audio clip gate) and hits the integrated
    # loudness target. It MUST decode + filter + encode, so it forces a
    # re-encode — a filter graph cannot ride on `-c copy`. loudnorm internally
    # upsamples to 192 kHz, so we resample back to a distribution rate.
    filter_args: list[str] = []
    if loudnorm:
        chain = f"loudnorm=I={loudnorm_i}:TP={loudnorm_tp}:LRA={loudnorm_lra}"
        if loudnorm_ar:
            chain += f",aresample={loudnorm_ar}"
        filter_args = ["-af", chain]
        mode = "reencode"

    if fmt == "wav":
        # A verified single-file wav has no concatenation-header problem to
        # collapse, so `mode`/`-c copy` don't apply — always one clean encode
        # straight to the delivery format.
        target_fmt = encode_format or "wav"
        codec_args = (
            [] if target_fmt == "wav"
            else ["-c:a", _REENCODE_CODEC.get(target_fmt, "libmp3lame"), "-b:a", bitrate]
        )
    else:
        target_fmt = fmt
        # Re-encode collapses the multi-segment headers; copy is the lossless
        # header-only repair. libmp3lame is always built into ffmpeg.
        if mode == "copy":
            codec_args = ["-c", "copy"]
        else:
            codec_args = ["-c:a", _REENCODE_CODEC.get(fmt, "libmp3lame"), "-b:a", bitrate]

    tmpdir = tempfile.mkdtemp(prefix="tts-remux-")
    try:
        src = os.path.join(tmpdir, f"in.{fmt}")
        dst = os.path.join(tmpdir, f"out.{target_fmt}")
        await asyncio.to_thread(_write_bytes, src, audio_bytes)
        proc = await asyncio.create_subprocess_exec(
            ffmpeg, "-hide_banner", "-loglevel", "error", "-y",
            "-i", src, *filter_args, *codec_args, dst,
            stdout=asyncio.subprocess.DEVNULL,
            stderr=asyncio.subprocess.PIPE,
        )
        _, stderr = await proc.communicate()
        if proc.returncode != 0 or not os.path.exists(dst):
            logger.warning(
                "[tts_service] audio normalize failed (mode=%s, rc=%s): %s — "
                "using raw audio",
                mode, proc.returncode,
                (stderr or b"").decode("utf-8", "replace")[:300],
            )
            return audio_bytes
        fixed = await asyncio.to_thread(_read_bytes, dst)
        if not fixed:
            return audio_bytes
        logger.info(
            "[tts_service] normalized %s → %s audio (mode=%s): %d → %d bytes",
            fmt, target_fmt, mode, len(audio_bytes), len(fixed),
        )
        return fixed
    except Exception as exc:
        logger.warning(
            "[tts_service] audio normalize errored: %s — using raw audio", exc
        )
        return audio_bytes
    finally:
        shutil.rmtree(tmpdir, ignore_errors=True)
