"""CaptionProvider — transcribe audio into time-aligned caption
segments.

The video pipeline's ``stitch`` Stage burns captions into the final
output and ships an SRT/VTT sidecar so YouTube auto-CC works
immediately. The transcription itself talks to whisper.cpp on
localhost OR a cloud API (AssemblyAI, Deepgram, OpenAI Whisper API).
Wrapping each as a CaptionProvider keeps the Stage agnostic.

Two implementation styles in mind:

- **Local-binary providers** (``whisper.cpp``, ``faster-whisper``
  Python bindings) — local CPU/GPU. Cost is electricity;
  ``is_local=True`` on the cost-guard record. Default first-line
  implementation.
- **Cloud-API providers** (AssemblyAI, Deepgram, OpenAI Whisper
  REST) — POST audio, get back JSON with segments. Cost is
  dollars; ``is_local=False``. Useful when local GPU is busy or
  when the operator wants vendor-grade accuracy on hard accents.

Register a CaptionProvider via ``pyproject.toml``:

.. code:: toml

    [project.entry-points."poindexter.caption_providers"]
    whisper_local = "cofounder_agent.services.caption_providers.whisper_local:WhisperLocalCaptionProvider"

Per-install config lives in
``app_settings.plugin.caption_provider.<name>`` —
binary path, model size (``"tiny"``, ``"base"``, ``"small"``,
``"medium"``, ``"large-v3"``), language hint, beam-size, cloud
API credentials, etc.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Literal, Protocol, runtime_checkable


@dataclass
class CaptionSegment:
    """One time-aligned snippet of transcribed text.

    Attributes:
        start_s: Segment start time in seconds from the audio
            origin.
        end_s: Segment end time in seconds. ``end_s > start_s``
            always; providers MUST reject zero-duration segments.
        text: Transcribed text. Plain UTF-8, no SRT escape
            characters. Whitespace pre-trimmed.
        speaker: Optional speaker label when the provider supports
            diarization. ``None`` for single-speaker audio or
            providers that don't diarize.
        confidence: Provider-reported confidence score 0.0–1.0.
            ``None`` when the provider doesn't expose one.
    """

    start_s: float
    end_s: float
    text: str
    speaker: str | None = None
    confidence: float | None = None


@dataclass
class CaptionResult:
    """Outcome of one transcription job.

    Attributes:
        success: True when the provider produced at least one
            usable segment. False on auth, format, or runtime
            error.
        segments: Time-aligned text snippets. Empty on failure.
        language: Auto-detected (or operator-supplied) language
            code (BCP-47, e.g. ``"en"``, ``"en-US"``). Empty
            string when unknown.
        srt_text: Full SRT-formatted document. Optional —
            providers that don't produce SRT natively can leave
            empty; the calling Stage builds SRT from
            :attr:`segments` instead.
        vtt_text: Full WebVTT-formatted document. Optional, same
            shape as :attr:`srt_text`.
        error: Human-readable failure summary. ``None`` on
            success.
        cost_usd: Cloud-API billing for this job. ``0.0`` for
            local providers.
        electricity_kwh: Local providers compute this from CPU/GPU
            watts × transcription wall-clock. Cloud providers
            leave at ``0.0``.
        metadata: Free-form per-provider extras (model name,
            API request ID, audio duration, etc.).
    """

    success: bool
    segments: list[CaptionSegment] = field(default_factory=list)
    language: str = ""
    srt_text: str = ""
    vtt_text: str = ""
    error: str | None = None
    cost_usd: float = 0.0
    electricity_kwh: float = 0.0
    metadata: dict[str, Any] = field(default_factory=dict)


@runtime_checkable
class CaptionProvider(Protocol):
    """Transcribe audio into time-aligned caption segments.

    Implementations MUST:

    1. Read config from ``site_config.get`` /
       ``site_config.get_secret`` under
       ``plugin.caption_provider.<self.name>.*``. Never env.
    2. Bail loudly when ``enabled=false`` instead of silently
       returning an empty result.
    3. Route the call through cost-guard with
       ``provider=f"caption.{self.name}"``,
       ``is_local`` set per implementation style.
    4. Return :class:`CaptionResult` with ``success=False`` for
       any recoverable error — never raise out of
       :meth:`transcribe` on a failure the caller should choose
       how to handle.

    Attributes:
        name: Provider name. Matches the entry_point key
            (``"whisper_local"``, ``"assemblyai"``, etc.).
        supported_languages: Languages the provider can transcribe
            with reasonable accuracy. Empty tuple = "auto-detect,
            we accept anything." Used at Stage time to skip
            providers when the operator has hinted a language the
            local Whisper model can't handle.
        supports_diarization: True when the provider populates
            ``CaptionSegment.speaker``. Compositors that need
            multi-speaker captions filter to providers that
            support this.
    """

    name: str
    supported_languages: tuple[str, ...]
    supports_diarization: bool

    async def transcribe(
        self,
        *,
        audio_path: str,
        language_hint: str = "",
        granularity: Literal["segment", "word"] = "segment",
        **kwargs: Any,
    ) -> CaptionResult:
        """Transcribe an audio file into time-aligned segments.

        Args:
            audio_path: Local filesystem path to the input audio.
                Common formats (mp3, wav, m4a, flac, ogg, webm)
                MUST be accepted; provider implementations may
                shell out to ffmpeg for format conversion.
            language_hint: Optional BCP-47 language code. When
                provided, providers SHOULD pass it to the
                underlying model to short-circuit auto-detect.
                Empty string = auto-detect.
            granularity: ``"segment"`` returns ~5-15s segments
                aligned to natural pauses (default).
                ``"word"`` returns one segment per word with
                tight timing — useful for caption-style burned-in
                karaoke effects. Providers that don't support
                word-level fall back to segment-level.
            **kwargs: Provider-specific extras (``model``,
                ``beam_size``, ``temperature``, etc.). Providers
                MUST NOT raise on unknown kwargs.

        Returns:
            :class:`CaptionResult` with the segments. Failures
            return ``success=False`` inside the result.
        """
        ...
