"""Catch a TTS chunk that stopped early (stdlib-only).

Chatterbox sometimes ends a generation before the end of its text: it emits
the stop token after the first sentence of a multi-sentence chunk and returns
clean, natural-sounding audio for that sentence alone. Nothing errors and
nothing is logged; the rest of the chunk is simply never spoken. A 2026-10-06
video narration lost "The contents? A highlight reel no one at OpenAI wanted
public. Two elements stand out starkly from this filing." that way. The same
chunk re-rendered in full the next day: the stop is intermittent, so a retry
usually fixes it.

The tell is speaking rate. A chunk's characters per second of trimmed audio
sit close to the rest of the same request's chunks (same voice, same settings):
across 41 production chunks every one fell within 0.73x-1.23x of the median.
A chunk that dropped a third of its text reads 1.5x or more. So each chunk is
measured against the median of its own request, with no fixed rate to tune per
voice. Short chunks (a few words) are noisy and are not judged.

Kept dependency-free (only ``re`` and ``statistics``) so it ships in the slim
sidecar image and tests without torch.
"""

from __future__ import annotations

import re
import statistics
from collections.abc import Sequence

DEFAULT_MAX_RATE_RATIO = 1.5
MIN_JUDGED_CHARS = 40
# The reference rate when a request has too few judgeable chunks for a median:
# the production median (16.4 chars/s, exaggeration 0.65, cfg_weight 0.30).
FALLBACK_CHARS_PER_SECOND = 16.4
MIN_REFERENCE_CHUNKS = 3

_SENTENCE_RE = re.compile(r"(?<=[.!?])\s+")


def chars_per_second(text: str, seconds: float) -> float:
    """Characters spoken per second of (trimmed) audio; inf for no audio."""
    chars = len((text or "").strip())
    return chars / seconds if seconds > 0 else float("inf")


def reference_rate(
    texts: Sequence[str],
    seconds: Sequence[float],
    *,
    min_chars: int = MIN_JUDGED_CHARS,
    fallback: float = FALLBACK_CHARS_PER_SECOND,
) -> float:
    """The request's typical speaking rate: the median over judgeable chunks."""
    rates = [
        chars_per_second(t, s)
        for t, s in zip(texts, seconds, strict=False)
        if len((t or "").strip()) >= min_chars and s > 0
    ]
    if len(rates) < MIN_REFERENCE_CHUNKS:
        return fallback
    return statistics.median(rates)


def is_truncated(
    text: str,
    seconds: float,
    reference: float,
    *,
    max_ratio: float = DEFAULT_MAX_RATE_RATIO,
    min_chars: int = MIN_JUDGED_CHARS,
) -> bool:
    """Too little audio for this much text: the generation stopped early."""
    if max_ratio <= 0 or len((text or "").strip()) < min_chars:
        return False
    return chars_per_second(text, seconds) > reference * max_ratio


def split_sentences(text: str) -> list[str]:
    """The chunk's sentences, for a retry that generates each one on its own
    (a single-sentence generation has no later sentence to drop)."""
    return [s.strip() for s in _SENTENCE_RE.split((text or "").strip()) if s.strip()]
