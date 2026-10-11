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


# Internal-pause detection works on 10ms frames, not samples: speech crosses
# zero constantly, so per-sample amplitude would call every zero crossing
# "silence". -40 dB below the chunk's own peak is quieter than a breath, so a
# breath inside a long pause splits it rather than being cut.
_PAUSE_FRAME_MS = 10.0
_PAUSE_FLOOR_DB = -40.0
# The splice is between two near-silent stretches, so it is inaudible anyway;
# a short crossfade guarantees no step at the join.
_SPLICE_FADE_MS = 10.0


def clamp_internal_silence(
    wav: np.ndarray,
    sample_rate: int,
    *,
    max_seconds: float,
    floor_db: float = _PAUSE_FLOOR_DB,
    frame_ms: float = _PAUSE_FRAME_MS,
) -> np.ndarray:
    """Shorten every pause INSIDE ``wav`` that runs past ``max_seconds``.

    Chatterbox now and then generates seconds of dead air in the middle of a
    chunk: after a sentence it has finished, or partway through a product name
    or a number. Rendering the same text three times put the pause after "per
    gigabyte." at 0.86s, 3.33s and 0.85s, so the gap is a per-generation
    lottery and no text rewrite reaches it. ``join_segments`` already makes the
    seams BETWEEN chunks deterministic; this does the same inside one.

    An over-long pause keeps its first and last ``max_seconds / 2``, so the
    natural decay of the word before it and the onset of the word after it are
    untouched; only the dead middle goes. Pauses at or under the cap, and the
    chunk's leading/trailing silence (``trim_edge_silence``'s job), are left
    alone. ``max_seconds <= 0`` disables the clamp.
    """
    if max_seconds <= 0 or wav.size == 0:
        return wav
    hop = max(1, int(sample_rate * frame_ms / 1000.0))
    n = wav.size // hop
    if n < 3:
        return wav
    peak = float(np.max(np.abs(wav)))
    if peak <= 0.0:
        return wav
    frames = wav[: n * hop].reshape(n, hop)
    rms = np.sqrt(np.mean(frames.astype(np.float64) ** 2, axis=1))
    quiet = rms < peak * (10.0 ** (floor_db / 20.0))

    # Silent runs as [start, end) frame indices.
    edges = np.diff(np.concatenate(([0], quiet.astype(np.int8), [0])))
    starts = np.flatnonzero(edges == 1)
    ends = np.flatnonzero(edges == -1)

    keep_half = int(sample_rate * max_seconds / 2.0)
    fade = max(1, int(sample_rate * _SPLICE_FADE_MS / 1000.0))
    pieces: list[np.ndarray] = []
    cursor = 0
    for s, e in zip(starts, ends, strict=True):
        if s == 0 or e >= n:
            continue  # edge silence: not a pause between words
        a, b = int(s) * hop, int(e) * hop
        if b - a <= 2 * keep_half:
            continue
        head_end = a + keep_half
        tail_start = b - keep_half
        pieces.append(wav[cursor:head_end])
        f = min(fade, keep_half)
        if f > 0 and pieces[-1].size >= f:
            ramp = np.linspace(1.0, 0.0, f, dtype=np.float32)
            mixed = pieces[-1][-f:] * ramp + wav[tail_start:tail_start + f] * ramp[::-1]
            pieces[-1] = np.concatenate([pieces[-1][:-f], mixed])
            cursor = tail_start + f
        else:
            cursor = tail_start
    if not pieces:
        return wav
    pieces.append(wav[cursor:])
    return np.concatenate(pieces).astype(np.float32)


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


__all__ = ["clamp_internal_silence", "join_segments", "trim_edge_silence"]
