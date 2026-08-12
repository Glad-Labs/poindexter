"""Chunk-joining for the TTS sidecars — trim each chunk's own silence, then
insert ONE deliberate gap.

Chatterbox generates each sentence-chunk independently, and every generation
carries its own leading and trailing near-silence of unpredictable length. The
sidecar used to `np.concatenate` those raw waveforms with a fixed 0.25s gap
between them, so the audible gap was really:

    chunk N trailing silence  +  0.25s  +  chunk N+1 leading silence

Measured on two shipped episodes (2026-08-12): the code only ever inserts
0.25s, yet gaps reached **3.46s**, and ~40% of chunk boundaries exceeded 1.2s
in BOTH — 25 of 61 boundaries on one, 8 of 20 on the other. Long scripts chunk
more, so they accumulate more dead air, which is why the longest drafts were
the ones rejected for "weird pauses between some words".

Trimming each chunk's edges before joining makes the boundary gap exactly the
value asked for, so pacing is deterministic instead of a per-generation
lottery.

Kept to numpy only (like the sibling `text_chunking`) so it drops into the slim
sidecar images and is unit-testable without torch / fastapi / soundfile.
"""

from __future__ import annotations

import numpy as np

# Edge samples are "silence" below this fraction of the chunk's own peak. A
# RELATIVE floor, not an absolute one: chunk loudness varies per generation, and
# an absolute threshold would over-trim a quiet chunk and under-trim a loud one.
_SILENCE_FLOOR_RATIO = 0.02
# Never trim right up to the waveform — a few ms of room keeps consonant onsets
# ("t", "k", "p") from being clipped into a click.
_KEEP_MARGIN_MS = 30.0


def trim_edge_silence(
    wav: np.ndarray,
    sample_rate: int,
    *,
    floor_ratio: float = _SILENCE_FLOOR_RATIO,
    keep_margin_ms: float = _KEEP_MARGIN_MS,
) -> np.ndarray:
    """Return ``wav`` without its leading / trailing near-silence.

    Returns the input unchanged when it is empty or entirely below the floor —
    a chunk that is all silence is a generation failure worth hearing about,
    and silently collapsing it to zero samples would hide it.
    """
    if wav.size == 0:
        return wav

    peak = float(np.max(np.abs(wav)))
    if peak <= 0.0:
        return wav
    loud = np.flatnonzero(np.abs(wav) > peak * floor_ratio)
    if loud.size == 0:
        return wav

    margin = max(0, int(sample_rate * keep_margin_ms / 1000.0))
    start = max(0, int(loud[0]) - margin)
    end = min(wav.size, int(loud[-1]) + 1 + margin)
    return wav[start:end]


def join_segments(
    segments: list[np.ndarray],
    sample_rate: int,
    *,
    gap_seconds: float,
    trim: bool = True,
) -> np.ndarray:
    """Concatenate chunk waveforms separated by exactly ``gap_seconds``.

    With ``trim`` on (the default) each chunk's own edge silence is removed
    first, so the gap between two chunks is the requested value rather than
    that value plus two unpredictable tails. ``trim=False`` restores the old
    raw-concatenation behaviour for A/B comparison.

    Returns a single-sample zero array for empty input, matching what the
    sidecar previously fell back to so the encoder always has something.
    """
    usable = [s for s in segments if s is not None and s.size > 0]
    if not usable:
        return np.zeros(1, dtype=np.float32)

    if trim:
        usable = [trim_edge_silence(s, sample_rate) for s in usable]
        usable = [s for s in usable if s.size > 0]
        if not usable:
            return np.zeros(1, dtype=np.float32)

    gap_len = max(0, int(sample_rate * max(0.0, gap_seconds)))
    if gap_len == 0 or len(usable) == 1:
        return np.concatenate(usable).astype(np.float32)

    gap = np.zeros(gap_len, dtype=np.float32)
    out: list[np.ndarray] = []
    for i, seg in enumerate(usable):
        out.append(seg)
        if i < len(usable) - 1:
            out.append(gap)
    return np.concatenate(out).astype(np.float32)


__all__ = ["join_segments", "trim_edge_silence"]
