"""ChatterboxTTSProvider — emotion-capable TTS via the chatterbox sidecar.

ResembleAI/chatterbox (MIT). Emotion via the ``exaggeration`` dial (0.0-1.0);
``cfg_weight`` controls pacing; ``audio_prompt_path`` pins a zero-shot voice
clone. All three are passed to the sidecar as non-standard OpenAI-body
fields. Reuses ``render_openai_tts`` for identical loudnorm normalization.
Wired into the live podcast pipeline (Phase 2) via
``podcast_service._generate_with_chatterbox`` when
``app_settings.podcast_tts_engine == "chatterbox"``.
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


def _as_float(value: Any, default: float) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


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
        fmt = str(cfg.get("response_format") or self.default_format).lower()
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

        audio = await render_openai_tts(
            base_url=base_url, model=model, voice=voice or "default",
            text=text, response_format=fmt, read_timeout=timeout_s,
            extra_body=extra_body,
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
            audio_format=fmt,
            file_size_bytes=size,
            metadata={
                "engine": "chatterbox",
                "exaggeration": exaggeration,
                "cfg_weight": cfg_weight,
            },
        )
