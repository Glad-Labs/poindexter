"""CosyVoice2TTSProvider — emotion-capable TTS via the cosyvoice2 sidecar.

FunAudioLLM/CosyVoice2-0.5B (Apache-2.0). Instruction-controllable emotion:
the ``instruct`` config string ("speak with excitement", "calm and measured")
is passed through to the sidecar as a non-standard OpenAI-body field.

Renders through the shared ``render_openai_tts`` helper so it reuses the same
EBU-R128 loudnorm normalization as the Speaches path — clips are directly
comparable in the bake-off. Not wired into the live pipeline (Phase 1).
"""

from __future__ import annotations

import asyncio
import logging
from pathlib import Path
from typing import Any

from plugins.tts_provider import TTSResult
from services.tts_service import render_openai_tts

logger = logging.getLogger(__name__)

_DEFAULT_BASE_URL = "http://cosyvoice2:8000/v1"
_DEFAULT_MODEL = "cosyvoice2"
_SAMPLE_RATE = 24000


def _as_float(value: Any, default: float) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


class CosyVoice2TTSProvider:
    """Render audio with the CosyVoice2 sidecar (emotion via ``instruct``)."""

    name = "cosyvoice2"
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
            raise ValueError("CosyVoice2TTSProvider: refusing to synthesize empty text")

        base_url = str(cfg.get("base_url") or _DEFAULT_BASE_URL)
        model = str(cfg.get("model") or _DEFAULT_MODEL)
        fmt = str(cfg.get("response_format") or self.default_format).lower()
        instruct = str(cfg.get("instruct") or "").strip()
        # CPU-only bake-off runs take minutes for a full paragraph; give the
        # client room past the 120s render_openai_tts default (see chatterbox).
        timeout_s = _as_float(cfg.get("timeout_s"), 600.0)

        extra_body: dict[str, Any] = {}
        if instruct:
            extra_body["instruct"] = instruct

        logger.info(
            "CosyVoice2TTSProvider: synthesizing %d chars (instruct=%r)",
            len(text), instruct,
        )

        audio = await render_openai_tts(
            base_url=base_url, model=model, voice=voice or "default",
            text=text, response_format=fmt, read_timeout=timeout_s,
            extra_body=extra_body or None,
        )
        if audio is None:
            raise RuntimeError(
                f"cosyvoice2 sidecar returned no audio ({base_url}) — is the "
                f"tts-hq profile up? `docker compose --profile tts-hq up -d cosyvoice2`"
            )

        output_path.parent.mkdir(parents=True, exist_ok=True)
        await asyncio.to_thread(output_path.write_bytes, audio)
        size = output_path.stat().st_size

        return TTSResult(
            audio_path=output_path,
            duration_seconds=max(1, len(text.split()) * 60 // 150),  # ~150 wpm est.
            voice=voice or "default",
            sample_rate=self.sample_rate_hz,
            audio_format=fmt,
            file_size_bytes=size,
            metadata={"engine": "cosyvoice2", "instruct": instruct},
        )
