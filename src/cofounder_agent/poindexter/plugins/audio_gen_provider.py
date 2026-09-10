"""AudioGenProvider — Protocol for music / SFX / ambient audio generation.

A generation-only sibling to :class:`ImageProvider` for the audio side
of the media pipeline. Distinct from a future :class:`TTSProvider`
(speech-from-text) — this Protocol covers **non-speech** audio:

- Ambient beds for video backgrounds
- Short SFX / stings (intro / outro for podcasts)
- Loops + transitions

Speech synthesis (Kokoro, edge-tts, Piper) is intentionally NOT covered
here — it has a different prompt shape (a script to be read aloud) and
a different return contract (one long file, voice metadata) so it gets
its own Protocol. Keeping the two cleanly separated avoids a leaky
abstraction where every implementation has to branch on "am I doing
music or speech?".

Register an AudioGenProvider via ``pyproject.toml``:

.. code:: toml

    [project.entry-points."poindexter.audio_gen_providers"]
    stable-audio-open-1.0 = (
        "cofounder_agent.services.audio_gen_providers."
        "stable_audio_open:StableAudioOpenProvider"
    )

Per-install config lives in ``app_settings.plugin.audio_gen_provider.<name>``.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Literal, Protocol, runtime_checkable

# The four production kinds Glad Labs uses today. ``Literal`` instead of
# Enum so plugin authors can write the string directly without an extra
# import — matches the style used elsewhere in the plugin contracts
# (e.g. ImageProvider.kind = "search" | "generate").
AudioKind = Literal["ambient", "sfx", "intro", "outro"]


@dataclass
class AudioGenResult:
    """A single audio clip returned by a provider.

    Either ``audio_bytes`` OR ``file_path`` must be set — providers that
    stream raw bytes (in-process generation) typically populate
    ``audio_bytes``; providers that hit a sidecar / inference server
    that writes to disk populate ``file_path``. Callers should prefer
    ``file_path`` when both are set (avoids re-writing a copy).

    Field semantics:

    - ``duration_s``: actual rendered duration in seconds (provider may
      not honor the requested duration exactly — Stable Audio Open
      generates ~0-47s clips and trims/pads).
    - ``sample_rate``: PCM sample rate (Hz). Stable Audio Open emits
      44100 Hz stereo; MusicGen emits 32000 Hz mono.
    - ``kind``: one of ``"ambient" | "sfx" | "intro" | "outro"``.
      Orchestrators use this to pick the right provider call without
      hardcoding prompt strings.
    - ``format``: file format (``"wav"``, ``"mp3"``, ``"ogg"``, ``"flac"``).
    - ``metadata``: provider-specific extras — model name, seed, prompt
      template that was used, license, server URL, etc.
    """

    audio_bytes: bytes | None = None
    file_path: str = ""
    duration_s: float = 0.0
    sample_rate: int = 0
    kind: AudioKind = "ambient"
    format: str = "wav"
    prompt: str = ""
    source: str = "unknown"  # provider name: stable-audio-open-1.0, musicgen, etc.
    metadata: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        # Either bytes or path must be present — silent empty results are
        # a footgun for callers who'd then write a 0-byte file. Validate
        # at construction so the error surfaces at the boundary.
        if not self.audio_bytes and not self.file_path:
            raise ValueError(
                "AudioGenResult requires either audio_bytes or file_path",
            )

    def to_dict(self) -> dict[str, Any]:
        return {
            "audio_bytes_len": len(self.audio_bytes) if self.audio_bytes else 0,
            "file_path": self.file_path,
            "duration_s": self.duration_s,
            "sample_rate": self.sample_rate,
            "kind": self.kind,
            "format": self.format,
            "prompt": self.prompt,
            "source": self.source,
            "metadata": self.metadata,
        }


@runtime_checkable
class AudioGenProvider(Protocol):
    """Audio-generation plugin contract.

    Implementations synthesize a short audio clip from a text prompt + a
    ``kind`` selector. Cold-start can be slow (model load on first use);
    callers should await with a generous timeout.

    Attributes:
        name: Unique plugin name (matches the entry_point key + the
            ``source`` label attached to each AudioGenResult).
        kinds: Tuple of supported ``AudioKind`` values. Orchestrators
            filter on this at dispatch time so a provider that only
            does ambient beds isn't asked for a 2-second sting.
    """

    name: str
    kinds: tuple[AudioKind, ...]

    async def generate(
        self,
        prompt: str,
        kind: AudioKind,
        config: dict[str, Any],
    ) -> AudioGenResult | None:
        """Synthesize one audio clip.

        Args:
            prompt: Free-text description of the audio. Examples:
                "warm cinematic synth pad, 80 bpm, no vocals"
                "rising tech sting with sub-bass and bell, 3s"
            kind: Which slot the audio fills — ambient bed, SFX, intro,
                outro. Providers may format the prompt differently per
                kind (template the prompt with kind-specific tokens
                from app_settings).
            config: Per-install config from
                ``app_settings.plugin.audio_gen_provider.<name>`` —
                server URL, default duration, prompt templates, output
                format, sample rate. The dispatcher seeds the reserved
                ``_site_config`` key so providers can read fallback
                settings without re-importing site_config.

        Returns:
            ``AudioGenResult`` on success, ``None`` when the provider
            cannot satisfy the request (model unavailable, kind not
            supported). Providers should raise on genuine failures
            (auth error, invalid config) so callers can fall back or
            fail loud per the project's "no silent fallback" rule.
        """
        ...
