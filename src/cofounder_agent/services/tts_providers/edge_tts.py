"""EdgeTTSProvider — Microsoft Edge cloud-TTS via the ``edge-tts`` package.

This is a thin TTSProvider wrapper around the engine the podcast pipeline
has used since GH-122 was filed. Default-registered so flipping
``app_settings.podcast_tts_engine`` between ``edge_tts`` (default) and a
new provider (``kokoro``, etc.) is a settings change, not a code path
change.

Edge TTS:

- Free, no API key, ~24 kHz mp3 output.
- Uses Microsoft's neural voice catalog (``en-US-AvaMultilingualNeural``,
  ``en-US-AndrewMultilingualNeural``, etc.).
- Voice rotation across episodes is the caller's job — the dispatcher
  (``services.podcast_service``) hashes ``post_id`` to pick a stable
  index, then passes ``voice=`` to ``synthesize()``.

Config (``plugin.tts_provider.edge_tts`` in app_settings):

- ``default_voice`` — voice to use when the caller passes ``voice=None``.
  Defaults to the first entry in ``services.podcast_service.VOICE_POOL``.

Per the project convention all TTS-text preprocessing (pronunciation /
acronym overrides) runs upstream in the pipeline; this provider receives
ready-to-speak text and only renders audio.
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

from plugins.tts_provider import TTSResult

logger = logging.getLogger(__name__)


# Default voice when the caller doesn't specify one. Mirrors the head of
# ``services.podcast_service.VOICE_POOL`` so behavior stays bit-identical
# to the legacy code path when ``podcast_tts_engine`` is unset.
_DEFAULT_VOICE = "en-US-AvaMultilingualNeural"


def _estimate_duration_from_text(text: str) -> int:
    """Rough duration estimate: ~150 words per minute for TTS.

    Mirrors the legacy ``services.podcast_service._estimate_duration_from_text``
    helper so episodes generated through this provider report durations
    that match what the pipeline previously logged.
    """
    word_count = len(text.split())
    return max(30, int(word_count / 150 * 60))


class EdgeTTSProvider:
    """Render audio via Microsoft Edge TTS (``edge-tts`` PyPI package)."""

    name = "edge_tts"
    sample_rate_hz = 24000
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
            raise ValueError("EdgeTTSProvider: refusing to synthesize empty text")

        chosen_voice = voice or str(cfg.get("default_voice") or _DEFAULT_VOICE)

        try:
            import edge_tts  # type: ignore[import-not-found]
        except ImportError as e:
            raise RuntimeError(
                "edge-tts package not installed. Run: pip install edge-tts",
            ) from e

        output_path.parent.mkdir(parents=True, exist_ok=True)

        try:
            communicate = edge_tts.Communicate(text, chosen_voice)
            await communicate.save(str(output_path))
        except Exception as e:
            # Clean up partial files so subsequent voice fallbacks start
            # from a clean slate (mirrors legacy behavior).
            if output_path.exists():
                output_path.unlink(missing_ok=True)
            raise RuntimeError(
                f"edge-tts synthesis failed for voice {chosen_voice!r}: "
                f"{type(e).__name__}: {e}",
            ) from e

        if not output_path.exists() or output_path.stat().st_size == 0:
            raise RuntimeError(
                f"edge-tts produced empty file with voice {chosen_voice!r}",
            )

        size = output_path.stat().st_size
        duration = _estimate_duration_from_text(text)
        logger.info(
            "EdgeTTSProvider: rendered %s (%d bytes, ~%ds, voice=%s)",
            output_path.name, size, duration, chosen_voice,
        )

        return TTSResult(
            audio_path=output_path,
            audio_bytes=b"",
            duration_seconds=duration,
            voice=chosen_voice,
            sample_rate=self.sample_rate_hz,
            audio_format=self.default_format,
            file_size_bytes=size,
            metadata={"engine": "edge-tts"},
        )
