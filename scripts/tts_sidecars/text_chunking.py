"""Sentence-chunking for the TTS bake-off sidecar (stdlib-only).

Neural TTS models like Chatterbox have a hard per-call acoustic-token
budget — roughly 1000 steps / ~40s of audio for Chatterbox. Feeding a full
~60s podcast paragraph in one `generate()` call silently truncates the tail.
Production TTS pipelines avoid this by splitting into sentences, synthesizing
each, and concatenating the waveforms. This module is the split half; the
shims own the generate-and-concatenate half.

Kept dependency-free (only `re`) so it drops into the slim sidecar images and
is unit-testable without importing torch/fastapi/soundfile.
"""

from __future__ import annotations

import re

# ~240 chars ≈ 40-45 words ≈ well under Chatterbox's ~1000-step budget, leaving
# headroom for content that samples more densely than average.
_DEFAULT_MAX_CHARS = 240


def chunk_text(text: str, max_chars: int = _DEFAULT_MAX_CHARS) -> list[str]:
    """Group `text` into chunks of whole sentences, each <= `max_chars`.

    Sentences are split on `.!?` followed by whitespace (punctuation retained).
    Sentences are packed greedily; a single sentence longer than `max_chars`
    becomes its own (over-budget) chunk rather than being dropped. Returns
    `[]` for empty input.
    """
    text = (text or "").strip()
    if not text:
        return []
    sentences = re.split(r"(?<=[.!?])\s+", text)
    chunks: list[str] = []
    current = ""
    for sentence in sentences:
        sentence = sentence.strip()
        if not sentence:
            continue
        if current and len(current) + 1 + len(sentence) > max_chars:
            chunks.append(current)
            current = sentence
        else:
            current = f"{current} {sentence}".strip()
    if current:
        chunks.append(current)
    return chunks or [text]
