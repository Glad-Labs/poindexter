"""Forced alignment of the known narration script onto ASR segment timings.

2026-08-03 (operator finding): burned-in captions carried "PHY 4" for Phi-4
and "gait" for "gate". Captions were the raw Whisper transcription of the TTS
audio — but ASR is only needed for *timings*; the ground-truth TEXT is the
narration script we synthesized from. This module maps the clean written
script onto the ASR segments so the burned captions read exactly what the
writer wrote (correct casing, real product names, no homophone guesses),
while keeping Whisper's segment timings.

Approach (segment-level, provider-agnostic — needs no word timestamps):

1. Tokenize the ASR transcript per segment and the caption script.
2. ``difflib.SequenceMatcher`` over *normalized* tokens (lowercase,
   punctuation-stripped) builds matching blocks; homophones and phonetic
   spellings ("gait"/"gate", "see eye see dee"/"CI/CD") land in short
   replace-blocks between matches.
3. A monotone ASR-index → script-index map (exact inside matching blocks,
   linear interpolation inside gaps) converts each segment's ASR token span
   into a script token span; consecutive spans partition the script, so every
   script token appears exactly once, in order.
4. The match fraction gates the whole rewrite: below the caller's floor the
   ASR text is kept as-is (fail-open — a bad alignment is worse than a
   homophone).
"""

from __future__ import annotations

import difflib
import math
import re
from dataclasses import replace
from typing import Any

from plugins.caption_provider import CaptionSegment

_TOKEN_NORM = re.compile(r"[^\w]+")


def _norm_token(tok: str) -> str:
    return _TOKEN_NORM.sub("", tok.lower())


def align_script_to_segments(
    segments: list[CaptionSegment],
    caption_text: str,
) -> tuple[list[CaptionSegment], float]:
    """Rewrite ``segments``' text with ``caption_text`` mapped onto their timings.

    Returns ``(new_segments, match_fraction)``. ``match_fraction`` is the share
    of ASR tokens that matched a script token (0.0–1.0) — the caller gates on
    it and keeps the original ASR captions when alignment quality is poor.
    Segments whose mapped script span is empty (pure ASR insertions) are
    dropped from the result. Never raises on odd input; degenerate cases
    return ``(segments, 0.0)`` untouched.
    """
    script_tokens = caption_text.split()
    asr_tokens: list[str] = []
    seg_spans: list[tuple[int, int]] = []  # [start, end) into asr_tokens per segment
    for seg in segments:
        start = len(asr_tokens)
        asr_tokens.extend((seg.text or "").split())
        seg_spans.append((start, len(asr_tokens)))

    if not script_tokens or not asr_tokens:
        return segments, 0.0

    norm_asr = [_norm_token(t) for t in asr_tokens]
    norm_script = [_norm_token(t) for t in script_tokens]
    matcher = difflib.SequenceMatcher(None, norm_asr, norm_script, autojunk=False)
    blocks = matcher.get_matching_blocks()  # terminates with a zero-length block
    matched = sum(b.size for b in blocks)
    fraction = matched / max(len(asr_tokens), 1)

    # Monotone ASR→script index map. Anchor points at every matching-block
    # token; linear interpolation across gap (replace/insert/delete) regions;
    # clamped monotone so segment spans can never regress.
    anchors: list[tuple[int, int]] = []
    for b in blocks:
        if b.size:
            anchors.append((b.a, b.b))
            anchors.append((b.a + b.size, b.b + b.size))
    if not anchors:
        return segments, fraction
    if anchors[0] != (0, 0):
        anchors.insert(0, (0, 0))
    end_anchor = (len(asr_tokens), len(script_tokens))
    if anchors[-1][0] < end_anchor[0] or anchors[-1][1] < end_anchor[1]:
        anchors.append(end_anchor)

    def to_script_idx(asr_idx: int) -> int:
        for (a0, s0), (a1, s1) in zip(anchors, anchors[1:], strict=False):
            if asr_idx <= a0:
                return s0
            if asr_idx <= a1:
                if a1 == a0:
                    return s1
                frac = (asr_idx - a0) / (a1 - a0)
                return round(s0 + frac * (s1 - s0))
        return len(script_tokens)

    out: list[CaptionSegment] = []
    prev_script_end = 0
    for i, ((a, b), seg) in enumerate(zip(seg_spans, segments, strict=True)):
        s_start = max(prev_script_end, to_script_idx(a))
        s_end = max(s_start, to_script_idx(b))
        if i == len(segments) - 1:
            s_end = len(script_tokens)  # never drop script tail
        prev_script_end = s_end
        text = " ".join(script_tokens[s_start:s_end]).strip()
        if text:
            out.append(replace(seg, text=text))

    if not out:
        return segments, fraction
    return out, fraction


def split_segments_for_display(
    segments: list[CaptionSegment],
    *,
    max_words: int,
    min_cue_seconds: float = 0.6,
) -> list[CaptionSegment]:
    """Re-cut sentence-sized ASR segments into short display cues.

    Whisper emits segments at sentence/phrase scale — 10-15 words is routine.
    Burned into a 9:16 frame at mobile-readable size, one such cue wraps into
    a frame-filling wall of text (2026-08-24 operator report). Short-form
    captioning convention is 3-8 words on screen at a time, so each segment
    over ``max_words`` is split into near-equal word chunks (sizes differ by
    at most one — never an orphan one-word tail), and each chunk's window is
    interpolated inside its parent segment proportional to text length.

    TTS narration is continuous speech with no mid-segment silences, so
    linear interpolation stays within ~±200ms of the true word timing —
    imperceptible on a caption. (The caption provider's ``granularity="word"``
    request would give exact word timestamps if this ever needs to tighten.)

    ``min_cue_seconds`` caps how finely a segment may be cut: the chunk count
    is reduced so no cue's window falls below it (a flashing sub-second cue is
    worse than an over-full one). ``max_words <= 0`` disables splitting.
    Timings never overlap, chunk boundaries are contiguous, and the last
    chunk ends exactly at the parent segment's ``end_s``. ``speaker`` /
    ``confidence`` carry through unchanged.
    """
    if max_words <= 0:
        return segments

    out: list[CaptionSegment] = []
    for seg in segments:
        words = (seg.text or "").split()
        duration = max(0.0, float(seg.end_s) - float(seg.start_s))
        n_chunks = math.ceil(len(words) / max_words) if words else 1
        if min_cue_seconds > 0 and duration > 0:
            n_chunks = min(n_chunks, max(1, int(duration / min_cue_seconds)))
        if n_chunks <= 1:
            out.append(seg)
            continue

        # Balanced sizes: base words per chunk, the first ``rem`` get one more.
        base, rem = divmod(len(words), n_chunks)
        chunks: list[list[str]] = []
        idx = 0
        for i in range(n_chunks):
            size = base + (1 if i < rem else 0)
            chunks.append(words[idx : idx + size])
            idx += size

        # Char-weighted timing: longer text ≈ longer speech, better than a
        # flat per-word share ("a big" vs "extraordinarily").
        weights = [sum(len(w) + 1 for w in chunk) for chunk in chunks]
        total_weight = sum(weights) or 1
        cursor = float(seg.start_s)
        consumed = 0
        for i, (chunk, weight) in enumerate(zip(chunks, weights, strict=True)):
            consumed += weight
            if i == len(chunks) - 1:
                end = float(seg.end_s)  # exact — no float drift on the tail
            else:
                end = float(seg.start_s) + duration * consumed / total_weight
            out.append(replace(seg, start_s=cursor, end_s=end, text=" ".join(chunk)))
            cursor = end
    return out


def retime_cues_to_words(
    cues: list[CaptionSegment],
    words: list[Any],
    *,
    lead_s: float = 0.12,
) -> list[CaptionSegment]:
    """Snap display-cue windows onto real word-level speech timings.

    The display cues carry the right TEXT (aligned script, chunked short) but
    their windows were interpolated inside Whisper segment spans — and
    interpolation drifted enough that the voice ran ahead of the text
    (2026-08-26 operator report). When the ASR provider returns word-level
    timestamps, each cue's window is re-anchored to reality: start = its
    first matched word's onset minus ``lead_s`` (captions conventionally
    LEAD speech by a beat — text appearing exactly at the word's onset still
    reads late), end = its last matched word's offset.

    Matching is the same normalized-token SequenceMatcher the aligner uses,
    so homophones/phonetic ASR spellings still anchor. A cue with no matched
    token keeps its interpolated window. Output windows are clamped
    monotone + non-overlapping (a lead never eats the previous cue), and
    every cue keeps a minimum visible window. ``words`` items need
    ``start_s`` / ``end_s`` / ``text`` attributes (``CaptionWord``); an
    empty list returns the cues untouched.
    """
    if not cues or not words:
        return cues

    cue_tokens: list[str] = []
    cue_spans: list[tuple[int, int]] = []  # [start, end) into cue_tokens per cue
    for cue in cues:
        start = len(cue_tokens)
        cue_tokens.extend(_norm_token(t) for t in (cue.text or "").split())
        cue_spans.append((start, len(cue_tokens)))
    word_tokens = [_norm_token(getattr(w, "text", "") or "") for w in words]
    if not cue_tokens or not word_tokens:
        return cues

    matcher = difflib.SequenceMatcher(
        None, cue_tokens, word_tokens, autojunk=False,
    )
    # cue-token index → word index, exact inside matching blocks.
    token_to_word: dict[int, int] = {}
    for block in matcher.get_matching_blocks():
        for offset in range(block.size):
            token_to_word[block.a + offset] = block.b + offset

    out: list[CaptionSegment] = []
    prev_end = 0.0
    min_window_s = 0.3
    for (a, b), cue in zip(cue_spans, cues, strict=True):
        matched = [token_to_word[i] for i in range(a, b) if i in token_to_word]
        if matched:
            start = float(words[matched[0]].start_s) - max(0.0, lead_s)
            end = float(words[matched[-1]].end_s)
        else:
            start, end = float(cue.start_s), float(cue.end_s)
        start = max(prev_end, start, 0.0)
        end = max(end, start + min_window_s)
        out.append(replace(cue, start_s=round(start, 3), end_s=round(end, 3)))
        prev_end = end
    return out


def segments_to_srt(segments: list[CaptionSegment]) -> str:
    """SRT document from segments — same format the caption providers emit."""
    if not segments:
        return ""

    def _ts(seconds: float) -> str:
        seconds = max(0.0, float(seconds))
        ms = int(round(seconds * 1000))
        h, rem = divmod(ms, 3_600_000)
        m, rem = divmod(rem, 60_000)
        s, ms = divmod(rem, 1000)
        return f"{h:02d}:{m:02d}:{s:02d},{ms:03d}"

    blocks: list[str] = []
    for index, seg in enumerate(segments, start=1):
        blocks.append(
            f"{index}\n{_ts(seg.start_s)} --> {_ts(seg.end_s)}\n{seg.text}\n"
        )
    return "\n".join(blocks)


__all__ = [
    "align_script_to_segments",
    "retime_cues_to_words",
    "segments_to_srt",
    "split_segments_for_display",
]
