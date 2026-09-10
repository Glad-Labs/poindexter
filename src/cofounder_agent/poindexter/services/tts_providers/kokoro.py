"""KokoroTTSProvider — Apache-2.0 82M-param local TTS (GH-122).

`hexgrad/Kokoro-82M <https://huggingface.co/hexgrad/Kokoro-82M>`_ is a
small, high-quality TTS model that runs comfortably on CPU and is
trivial on a 5090. License is **Apache-2.0** (commercial-use clean,
unlike the Edge TTS terms which are tied to Microsoft's services).

Why ship this:

- Quality is at the top of the TTS Arena leaderboard.
- 82M parameters → runs locally with zero ongoing per-episode cost.
- Voice catalog is large enough for podcast voice rotation across
  episodes.
- Replaces a cloud dependency with a fully-offline path.

Install (handled at runtime via the ``kokoro`` PyPI package):

.. code:: bash

    pip install kokoro>=0.9.2 soundfile
    apt-get install espeak-ng

The model weights download from HuggingFace on first use. We don't
ship them in the container image.

Config (``plugin.tts_provider.kokoro`` in app_settings):

- ``default_voice`` — voice id, e.g. ``"af_heart"``. Default ``"af_heart"``.
- ``lang_code`` — Kokoro language code (``"a"`` = American English,
  ``"b"`` = British English). Default ``"a"``.
- ``speed`` — playback speed multiplier (1.0 = natural). Default 1.0.

Output is 24 kHz mono. We write ``.wav`` from soundfile and let the
caller transcode to mp3 if Apple Podcasts distribution is required —
the podcast service handles that today.
"""

from __future__ import annotations

import asyncio
import logging
from pathlib import Path
from typing import Any

from plugins.tts_provider import TTSResult

logger = logging.getLogger(__name__)


# Defaults match the model card's quickstart so a default-config install
# produces working audio without per-operator tuning.
_DEFAULT_VOICE = "af_heart"
_DEFAULT_LANG_CODE = "a"  # American English
_KOKORO_SAMPLE_RATE = 24000


# Module-level pipeline cache — lazy-initialized on first synthesis.
# Kokoro's KPipeline is expensive to construct (loads model weights);
# subsequent calls reuse the same instance. Keyed by (lang_code,) since
# the pipeline is language-scoped.
_PIPELINE_CACHE: dict[str, Any] = {}


def _get_pipeline(lang_code: str) -> Any:
    """Return a cached ``KPipeline`` for ``lang_code``, building if needed.

    Importing ``kokoro`` is expensive (it pulls in torch + the model
    weights on first load), so we defer it until first synthesis. Tests
    monkeypatch this function to inject a fake pipeline.
    """
    cached = _PIPELINE_CACHE.get(lang_code)
    if cached is not None:
        return cached

    try:
        from kokoro import KPipeline  # type: ignore[import-not-found]
    except ImportError as e:
        raise RuntimeError(
            "kokoro package not installed. Run: pip install kokoro>=0.9.2 soundfile",
        ) from e

    pipeline = KPipeline(lang_code=lang_code)
    _PIPELINE_CACHE[lang_code] = pipeline
    return pipeline


def _write_wav(path: Path, audio: Any, sample_rate: int) -> None:
    """Write a soundfile-compatible audio payload to ``path`` as WAV.

    Split out so tests can monkeypatch without depending on soundfile +
    numpy being installed in the test environment.
    """
    try:
        import soundfile as sf  # type: ignore[import-not-found]
    except ImportError as e:
        raise RuntimeError(
            "soundfile not installed. Run: pip install soundfile",
        ) from e

    sf.write(str(path), audio, sample_rate)


def _synthesize_blocking(
    text: str,
    output_path: Path,
    voice: str,
    lang_code: str,
    speed: float,
) -> int:
    """Run Kokoro synthesis on the calling thread.

    Kokoro's ``KPipeline`` is synchronous (and CPU/GPU-bound under
    PyTorch), so we wrap it in ``asyncio.to_thread`` from the async
    ``synthesize`` method. This helper holds the actual blocking work
    so that wrapper stays small.

    Returns the duration in seconds (estimated from sample count).
    """
    pipeline = _get_pipeline(lang_code)

    # Kokoro's pipeline yields (graphemes, phonemes, audio) per chunk.
    # For a podcast-length script the model emits multiple chunks; we
    # concatenate them before writing. ``import numpy`` only happens
    # when we actually have something to render, so test environments
    # that don't install numpy can still import this module.
    import numpy as np  # type: ignore[import-not-found]

    chunks: list[Any] = []
    generator = pipeline(text, voice=voice, speed=speed)
    for _gs, _ps, audio in generator:
        chunks.append(audio)

    if not chunks:
        raise RuntimeError(
            "Kokoro pipeline yielded no audio chunks — empty or invalid input",
        )

    full_audio = np.concatenate(chunks) if len(chunks) > 1 else chunks[0]
    output_path.parent.mkdir(parents=True, exist_ok=True)
    _write_wav(output_path, full_audio, _KOKORO_SAMPLE_RATE)

    # Sample-count → seconds; reliable since we know the rate.
    sample_count = int(getattr(full_audio, "shape", [0])[0]) or len(full_audio)
    return max(1, int(sample_count / _KOKORO_SAMPLE_RATE))


class KokoroTTSProvider:
    """Render audio with the Kokoro-82M local TTS model."""

    name = "kokoro"
    sample_rate_hz = _KOKORO_SAMPLE_RATE
    default_format = "wav"

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
            raise ValueError("KokoroTTSProvider: refusing to synthesize empty text")

        chosen_voice = voice or str(cfg.get("default_voice") or _DEFAULT_VOICE)
        lang_code = str(cfg.get("lang_code") or _DEFAULT_LANG_CODE)
        try:
            speed = float(cfg.get("speed", 1.0) or 1.0)
        except (TypeError, ValueError):
            speed = 1.0

        logger.info(
            "KokoroTTSProvider: synthesizing %d chars (voice=%s, lang=%s, speed=%.2f)",
            len(text), chosen_voice, lang_code, speed,
        )

        # Run the blocking pytorch pipeline off the event loop.
        try:
            duration = await asyncio.to_thread(
                _synthesize_blocking,
                text, output_path, chosen_voice, lang_code, speed,
            )
        except Exception as e:
            if output_path.exists():
                output_path.unlink(missing_ok=True)
            raise RuntimeError(
                f"Kokoro synthesis failed (voice={chosen_voice!r}, "
                f"lang={lang_code!r}): {type(e).__name__}: {e}",
            ) from e

        if not output_path.exists() or output_path.stat().st_size == 0:
            raise RuntimeError(
                f"Kokoro produced empty file with voice {chosen_voice!r}",
            )

        size = output_path.stat().st_size
        logger.info(
            "KokoroTTSProvider: rendered %s (%d bytes, ~%ds, voice=%s)",
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
            metadata={
                "engine": "kokoro-82M",
                "lang_code": lang_code,
                "speed": speed,
            },
        )
