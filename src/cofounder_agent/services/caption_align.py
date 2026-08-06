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
import re
from dataclasses import replace

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


__all__ = ["align_script_to_segments", "segments_to_srt"]
