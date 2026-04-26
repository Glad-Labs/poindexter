"""TTSProvider — text-to-speech Protocol for the podcast / video pipelines.

A TTSProvider takes a fully-prepared **spoken-form** script and renders
it as audio. The pipeline calls one provider per episode; selection is
done by name through ``app_settings.podcast_tts_engine`` (and, when the
video stack lands, an analogous video-side key).

This is the engine-agnostic seam that lets the pipeline swap between
``edge_tts`` (Microsoft Edge cloud-TTS, default), ``kokoro`` (the
Apache-2.0 82M-param local model, GH-122), and any future provider
without code changes — flip a setting, restart nothing.

All TTS-text preprocessing (``tts_pronunciations``,
``tts_acronym_replacements``, the regex-based speech normalizer in
``services.podcast_service``) runs **before** the provider is called.
Providers receive ready-to-speak text and only worry about audio
synthesis.

Register a TTSProvider via ``pyproject.toml``:

.. code:: toml

    [project.entry-points."poindexter.tts_providers"]
    edge_tts = "cofounder_agent.services.tts_providers.edge_tts:EdgeTTSProvider"
    kokoro = "cofounder_agent.services.tts_providers.kokoro:KokoroTTSProvider"

Per-install config lives in ``app_settings`` under
``plugin.tts_provider.<name>`` (matches the convention the
``ImageProvider`` plugins follow).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Protocol, runtime_checkable


@dataclass
class TTSResult:
    """Audio rendered by a TTS provider.

    Either ``audio_path`` (the provider wrote a finished file to disk)
    or ``audio_bytes`` (in-memory payload the caller will save) is
    populated. Providers SHOULD prefer ``audio_path`` so the caller
    doesn't have to round-trip a multi-megabyte buffer through the
    event loop, but in-memory is supported for tests + future streaming
    callers.

    Field semantics:

    - ``audio_path`` — absolute path to the written audio file. ``None``
      when the provider returned bytes only.
    - ``audio_bytes`` — raw audio payload (provider-defined format).
      Empty when the provider wrote a file directly.
    - ``duration_seconds`` — playback duration. Providers should
      estimate from word count when they can't measure exactly; the
      podcast RSS feed uses this for ``<itunes:duration>``.
    - ``voice`` — voice identifier the provider used. Logged + stored
      in episode metadata for A/B comparisons.
    - ``sample_rate`` — sample rate in Hz. 24000 for Kokoro, 24000 for
      edge-tts (mp3), etc.
    - ``audio_format`` — extension-style format tag: ``"mp3"``,
      ``"wav"``, ``"opus"``. Callers use this to decide whether to
      transcode for distribution (Apple Podcasts wants mp3).
    - ``file_size_bytes`` — convenience field for callers that already
      need the size; matches the legacy ``EpisodeResult`` shape.
    - ``metadata`` — provider-specific extras (model version, voice
      pack, language code). Surfaces in episode detail UIs.
    """

    audio_path: Path | None = None
    audio_bytes: bytes = b""
    duration_seconds: int = 0
    voice: str = ""
    sample_rate: int = 24000
    audio_format: str = "mp3"
    file_size_bytes: int = 0
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "audio_path": str(self.audio_path) if self.audio_path else None,
            "audio_bytes_len": len(self.audio_bytes),
            "duration_seconds": self.duration_seconds,
            "voice": self.voice,
            "sample_rate": self.sample_rate,
            "audio_format": self.audio_format,
            "file_size_bytes": self.file_size_bytes,
            "metadata": self.metadata,
        }


@runtime_checkable
class TTSProvider(Protocol):
    """Text-to-speech plugin contract.

    Implementations render a pre-normalized script to audio. They MUST
    be safe to call concurrently from the worker's asyncio loop —
    blocking SDKs (Kokoro, anything pytorch-backed) should run their
    synthesis under ``asyncio.to_thread``.

    Attributes:
        name: Unique plugin name (matches the entry_point key + the
            ``voice`` source label downstream code logs). Settings under
            ``plugin.tts_provider.<name>`` are routed by this string.
        sample_rate_hz: Native sample rate the provider produces.
            Surfaced as a class attribute so the pipeline can route
            mixed-rate episodes correctly without instantiating the
            provider just to discover the rate.
        default_format: Default audio_format the provider emits unless
            overridden in config (``"mp3"`` / ``"wav"`` / ``"opus"``).
    """

    name: str
    sample_rate_hz: int
    default_format: str

    async def synthesize(
        self,
        text: str,
        output_path: Path,
        *,
        voice: str | None = None,
        config: dict[str, Any] | None = None,
    ) -> TTSResult:
        """Render ``text`` to audio and write it to ``output_path``.

        Args:
            text: Already-normalized spoken-form script. The pipeline
                runs ``tts_pronunciations`` /
                ``tts_acronym_replacements`` substitution + the
                ``_normalize_for_speech`` regex pass before calling the
                provider; the provider should NOT re-normalize.
            output_path: Absolute path the provider writes to. The
                caller has already created the parent directory and
                claimed the filename.
            voice: Voice identifier (provider-specific). When ``None``,
                the provider picks a default — usually from its config
                blob or a hardcoded fallback. Pass an explicit value to
                exercise voice rotation across episodes.
            config: Per-install config from
                ``app_settings.plugin.tts_provider.<name>`` plus any
                dispatcher-injected reserved keys (``_site_config``).
                ``None`` is treated as ``{}``.

        Returns:
            :class:`TTSResult` with ``audio_path`` populated and
            ``file_size_bytes`` reflecting the rendered file. Providers
            should raise on failure (auth, model load, IO) rather than
            return an empty result — the dispatcher catches and falls
            back to the next configured provider.
        """
        ...
