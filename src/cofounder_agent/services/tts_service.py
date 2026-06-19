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
  input into segments internally and byte-concatenates them, so a
  multi-segment WAV has only the FIRST segment's RIFF header valid and
  players stop at ~24s. Concatenated MP3 frames play to completion.

Never raises — callers in generate_media_scripts treat audio as
best-effort; a missing Speaches container is logged, not fatal.
"""

from __future__ import annotations

import logging
from typing import Any

import httpx

logger = logging.getLogger(__name__)

_DEFAULT_BASE_URL = "http://speaches:8000/v1"
_DEFAULT_VOICE = "bf_emma"
_DEFAULT_MODEL = "speaches-ai/Kokoro-82M-v1.0-ONNX"
# mp3, NOT wav: Speaches byte-concatenates per-segment WAVs for long input,
# leaving only the first segment's RIFF header valid so players cut off at
# ~24s. Self-synchronizing MP3 frames concatenate cleanly and play in full.
_DEFAULT_FORMAT = "mp3"
_HTTP_TIMEOUT = httpx.Timeout(120.0, connect=5.0)


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

    logger.info(
        "[tts_service] TTS synthesized: %d bytes (voice=%s, fmt=%s)",
        len(audio_bytes), voice, fmt,
    )

    if output_path:
        try:
            import asyncio
            await asyncio.to_thread(_write_bytes, output_path, audio_bytes)
            logger.info("[tts_service] Wrote podcast audio to %s", output_path)
        except Exception as exc:
            logger.warning("[tts_service] Failed to write %s: %s", output_path, exc)

    return audio_bytes


def _write_bytes(path: str, data: bytes) -> None:
    """Sync helper for asyncio.to_thread — avoid blocking the event loop."""
    with open(path, "wb") as f:
        f.write(data)
