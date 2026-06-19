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
  are all present, but the stream's duration header still reports only
  the first segment — so we remux with ffmpeg ``-c copy`` (losslessly
  rewriting a correct whole-file header) before returning. Without the
  remux, players honor the short header and cut off mid-episode.
- podcast_tts_remux_enabled true  — flip false to skip the ffmpeg
  header remux (e.g. a TTS provider without the concat bug, or hosts
  with no ffmpeg). The remux is fail-soft regardless.

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

# Formats whose duration header we repair via an ffmpeg `-c copy` remux after
# Speaches byte-concatenates its internal segments (see module docstring).
# Self-synchronizing / streamable containers only — a concatenated WAV is NOT
# listed because `-c copy` would read just the first RIFF chunk and truncate.
_REMUX_FORMATS = frozenset({"mp3", "aac", "opus"})


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

    base_url = _resolve(site_config, "podcast_tts_base_url", _DEFAULT_BASE_URL).rstrip("/")
    voice = voice or _resolve(site_config, "podcast_tts_voice", _DEFAULT_VOICE)
    model = _resolve(site_config, "podcast_tts_model", _DEFAULT_MODEL)
    fmt = _resolve(site_config, "podcast_tts_format", _DEFAULT_FORMAT).lower()

    try:
        async with httpx.AsyncClient(timeout=_HTTP_TIMEOUT) as client:
            resp = await client.post(
                f"{base_url}/audio/speech",
                headers={"Authorization": "Bearer speaches"},
                json={
                    "model": model,
                    "voice": voice,
                    "input": text,
                    "response_format": fmt,
                },
            )
    except Exception as exc:
        logger.warning(
            "[tts_service] Speaches unreachable at %s: %s — "
            "podcast audio skipped. Check poindexter-speaches container.",
            base_url, exc,
        )
        return None

    if resp.status_code != 200:
        logger.warning(
            "[tts_service] Speaches returned %d: %s",
            resp.status_code, (resp.text or "")[:200],
        )
        return None

    audio_bytes = resp.content
    if not audio_bytes:
        logger.warning("[tts_service] Speaches returned empty body")
        return None

    # Speaches byte-concatenates its internal segments, leaving only the first
    # segment's duration in the stream header → players cut off mid-episode.
    # Remux (lossless `-c copy`) to rewrite a correct whole-file header. This
    # is the single TTS boundary, so podcast AND video narration are covered.
    if _resolve_bool(site_config, "podcast_tts_remux_enabled", True):
        audio_bytes = await _remux_concatenated_audio(audio_bytes, fmt)

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


async def _remux_concatenated_audio(audio_bytes: bytes, fmt: str) -> bytes:
    """Rewrite the container duration header so players don't truncate.

    Speaches byte-concatenates its per-segment audio for long input, leaving
    only the FIRST segment's duration in the stream header — so players cut
    off mid-episode even though every frame is present. An ffmpeg ``-c copy``
    remux re-reads all frames and writes one correct whole-file header,
    losslessly (no re-encode). Fail-soft: returns the original bytes on any
    error, and is a no-op for non-self-synchronizing formats (a concatenated
    WAV would truncate to segment 1 under ``-c copy``).
    """
    if fmt not in _REMUX_FORMATS:
        return audio_bytes
    ffmpeg = shutil.which("ffmpeg")
    if not ffmpeg:
        logger.warning(
            "[tts_service] ffmpeg not found — skipping duration-header remux; "
            "podcast/narration may report a short duration and cut off. Install "
            "ffmpeg or set podcast_tts_remux_enabled=false to silence this."
        )
        return audio_bytes

    tmpdir = tempfile.mkdtemp(prefix="tts-remux-")
    try:
        src = os.path.join(tmpdir, f"in.{fmt}")
        dst = os.path.join(tmpdir, f"out.{fmt}")
        await asyncio.to_thread(_write_bytes, src, audio_bytes)
        proc = await asyncio.create_subprocess_exec(
            ffmpeg, "-hide_banner", "-loglevel", "error", "-y",
            "-i", src, "-c", "copy", dst,
            stdout=asyncio.subprocess.DEVNULL,
            stderr=asyncio.subprocess.PIPE,
        )
        _, stderr = await proc.communicate()
        if proc.returncode != 0 or not os.path.exists(dst):
            logger.warning(
                "[tts_service] duration remux failed (rc=%s): %s — using raw audio",
                proc.returncode,
                (stderr or b"").decode("utf-8", "replace")[:300],
            )
            return audio_bytes
        fixed = await asyncio.to_thread(_read_bytes, dst)
        if not fixed:
            return audio_bytes
        logger.info(
            "[tts_service] remuxed concatenated %s audio: %d → %d bytes "
            "(duration header rewritten)",
            fmt, len(audio_bytes), len(fixed),
        )
        return fixed
    except Exception as exc:
        logger.warning(
            "[tts_service] duration remux errored: %s — using raw audio", exc
        )
        return audio_bytes
    finally:
        shutil.rmtree(tmpdir, ignore_errors=True)
