"""ChatterboxTTSProvider — emotion-capable TTS via the chatterbox sidecar.

ResembleAI/chatterbox (MIT). Emotion via the ``exaggeration`` dial (0.0-1.0);
``cfg_weight`` controls pacing; ``audio_prompt_path`` pins a zero-shot voice
clone. All three are passed to the sidecar as non-standard OpenAI-body
fields. Reuses ``render_openai_tts`` for identical loudnorm normalization.
Wired into the live podcast pipeline (Phase 2) via
``podcast_service._generate_with_chatterbox`` when
``app_settings.podcast_tts_engine == "chatterbox"``.

**Wire format is always wav, never the delivery format** (audio-fidelity
fix). The sidecar (``scripts/tts_sidecars/chatterbox_server.py``) concatenates
raw PCM samples across sentence-chunks BEFORE its own single ffmpeg encode, so
its wav response is always one valid file — unlike Speaches, which
byte-concatenates already-ENCODED segments and would truncate a wav to the
first RIFF header. Requesting wav and letting ``render_openai_tts`` do exactly
one encode straight to the delivery format (``response_format`` in ``config``)
avoids decoding an already-lossy mp3 response and re-encoding it a second time.
"""

from __future__ import annotations

import asyncio
import logging
from pathlib import Path
from typing import Any

from plugins.tts_provider import TTSResult
from services.tts_service import render_openai_tts

logger = logging.getLogger(__name__)

_DEFAULT_BASE_URL = "http://chatterbox:8000/v1"
_DEFAULT_MODEL = "chatterbox"
_SAMPLE_RATE = 24000

# Requested from the sidecar regardless of the configured delivery format —
# see the module docstring. Not operator-configurable: it's a property of
# how THIS sidecar safely transports audio, not a quality/delivery choice.
_WIRE_FORMAT = "wav"


def _as_float(value: Any, default: float) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _as_bool(value: Any, default: bool) -> bool:
    if value is None:
        return default
    if isinstance(value, bool):
        return value
    s = str(value).strip().lower()
    return default if not s else s in ("true", "1", "yes", "on")


class ChatterboxTTSProvider:
    """Render audio with the Chatterbox sidecar (emotion via exaggeration)."""

    name = "chatterbox"
    sample_rate_hz = _SAMPLE_RATE
    default_format = "mp3"

    async def synthesize(
        self,
        text: str,
        output_path: Path,
        *,
        voice: str | None = None,
        config: dict[str, Any] | None = None,
    ) -> TTSResult:
        cfg = config or {}
        if not text.strip():
            raise ValueError("ChatterboxTTSProvider: refusing to synthesize empty text")

        base_url = str(cfg.get("base_url") or _DEFAULT_BASE_URL)
        model = str(cfg.get("model") or _DEFAULT_MODEL)
        # The DELIVERY format the rest of the pipeline expects (podcast file /
        # video narration file) — NOT what we request from the sidecar, which
        # is always _WIRE_FORMAT (see module docstring).
        delivery_fmt = str(cfg.get("response_format") or self.default_format).lower()
        exaggeration = _as_float(cfg.get("exaggeration"), 0.5)
        cfg_weight = _as_float(cfg.get("cfg_weight"), 0.5)
        # CPU-only bake-off runs (no spare VRAM) take minutes for a full
        # paragraph — the sidecar chunks by sentence and generates serially,
        # so give the client room past the 120s render_openai_tts default.
        timeout_s = _as_float(cfg.get("timeout_s"), 600.0)
        # Voice-clone reference (a path inside the chatterbox container). Empty
        # string is the app_settings unset sentinel — omit rather than send ''
        # (chatterbox_server.py os.path.exists-checks any truthy value and 400s
        # on a miss), so unset falls back to the sidecar's built-in voice.
        audio_prompt_path = str(cfg.get("audio_prompt_path") or "").strip()

        logger.info(
            "ChatterboxTTSProvider: synthesizing %d chars (exaggeration=%.2f)",
            len(text), exaggeration,
        )

        extra_body: dict[str, Any] = {
            "exaggeration": exaggeration, "cfg_weight": cfg_weight,
        }
        if audio_prompt_path:
            extra_body["audio_prompt_path"] = audio_prompt_path
        # Inter-chunk silence. Forwarded only when configured, so an unset key
        # leaves the sidecar's own default in charge (same contract as the
        # loudnorm/remux keys below) rather than duplicating the value here.
        if str(cfg.get("chunk_gap_seconds") or "").strip():
            extra_body["gap_seconds"] = _as_float(cfg.get("chunk_gap_seconds"), 0.25)

        # remux_bitrate / loudnorm_* are forwarded ONLY when the caller
        # (podcast_service._generate_with_chatterbox) actually configured
        # them — omitting an unset key lets render_openai_tts's own default
        # win, so this file never duplicates (and can't drift from) the real
        # default values.
        render_kwargs: dict[str, Any] = {}
        if cfg.get("remux_bitrate"):
            render_kwargs["remux_bitrate"] = str(cfg["remux_bitrate"])
        if "loudnorm_enabled" in cfg:
            render_kwargs["loudnorm_enabled"] = _as_bool(cfg.get("loudnorm_enabled"), True)
        if cfg.get("loudnorm_i"):
            render_kwargs["loudnorm_i"] = str(cfg["loudnorm_i"])
        if cfg.get("loudnorm_tp"):
            render_kwargs["loudnorm_tp"] = str(cfg["loudnorm_tp"])
        if cfg.get("loudnorm_lra"):
            render_kwargs["loudnorm_lra"] = str(cfg["loudnorm_lra"])
        if cfg.get("loudnorm_ar"):
            render_kwargs["loudnorm_ar"] = str(cfg["loudnorm_ar"])
        # Pace. Chatterbox clones timbre from audio_prompt_path but NOT the
        # reference's speaking rate, so pace is corrected downstream in the
        # existing loudnorm pass rather than by re-recording a slower
        # reference — see _DEFAULT_ATEMPO in services/tts_service.py.
        if cfg.get("atempo"):
            render_kwargs["atempo"] = str(cfg["atempo"])

        audio = await render_openai_tts(
            base_url=base_url, model=model, voice=voice or "default",
            text=text, response_format=_WIRE_FORMAT, read_timeout=timeout_s,
            extra_body=extra_body, encode_format=delivery_fmt, **render_kwargs,
        )
        if audio is None:
            raise RuntimeError(
                f"chatterbox sidecar returned no audio ({base_url}) — is the "
                f"tts-hq profile up? `docker compose --profile tts-hq up -d chatterbox`"
            )

        output_path.parent.mkdir(parents=True, exist_ok=True)
        await asyncio.to_thread(output_path.write_bytes, audio)
        size = output_path.stat().st_size

        return TTSResult(
            audio_path=output_path,
            duration_seconds=max(1, len(text.split()) * 60 // 150),
            voice=voice or "default",
            sample_rate=self.sample_rate_hz,
            audio_format=delivery_fmt,
            file_size_bytes=size,
            metadata={
                "engine": "chatterbox",
                "exaggeration": exaggeration,
                "cfg_weight": cfg_weight,
            },
        )
