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

from poindexter.plugins.caption_provider import CaptionSegment

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


# Punctuation that ends a clause — a caption may cut AFTER a word carrying one.
_CLAUSE_END = (",", ";", ":", ".", "!", "?", "\u2014", "\u2013", "\u2026")
_CLOSERS = "\"')\u201d\u2019"
# A cue must hold at least this many words; a 1-word cue is a flash, not a line.
_MIN_CUE_WORDS = 2


def _ends_clause(word: str) -> bool:
    return word.rstrip(_CLOSERS).endswith(_CLAUSE_END)


def _cut_on_clauses(
    words: list[str], *, max_words: int, max_chunks: int,
) -> list[list[str]] | None:
    """Cut ``words`` into cues of at most ``max_words``, ending cues on clause
    punctuation where a boundary falls inside the window.

    Balanced word-count chunks read as machine-chopped: every cue in the
    2026-09-21 review of the newest short was a mid-clause fragment —
    "keeping users engaged without leaving" / "information within posts
    themselves, not" / "If your content doesn't". The text handed here is the
    CLEAN SCRIPT when alignment ran (``media_transcribe_narration``), so the
    writer's punctuation is available and is the right place to cut.

    For each cue the cut lands on the LAST clause boundary within the window
    ``[_MIN_CUE_WORDS, max_words]`` words from the cue start; with no boundary
    in the window the cue takes the full ``max_words``. A tail shorter than
    ``_MIN_CUE_WORDS`` is folded into the previous cue when that stays within
    ``max_words + 1`` (one word over beats an orphan), otherwise the last two
    cues are rebalanced.

    Returns ``None`` — meaning "use the balanced split" — when the text
    carries no clause punctuation at all, or when honouring the punctuation
    would exceed ``max_chunks`` (the ``min_cue_seconds`` cap). So unpunctuated
    input is chunked exactly as before.
    """
    if not any(_ends_clause(w) for w in words):
        return None
    chunks: list[list[str]] = []
    i, n = 0, len(words)
    while i < n:
        remaining = n - i
        if remaining <= max_words:
            chunks.append(words[i:])
            break
        cut = None
        lo = i + max(_MIN_CUE_WORDS, 1)
        hi = i + max_words
        for j in range(hi, lo - 1, -1):
            if _ends_clause(words[j - 1]):
                cut = j
                break
        if cut is None:
            # No boundary within reach: this clause is longer than a cue.
            # Balance the cut across the clause rather than taking a greedy
            # max_words and leaving "...in the" / "post itself." — split the
            # words up to the clause's end (or the text's) into near-equal
            # cues, and take the first of them now.
            clause_end = n
            for j in range(i, n):
                if _ends_clause(words[j]):
                    clause_end = j + 1
                    break
            clause_len = clause_end - i
            pieces = math.ceil(clause_len / max_words)
            cut = i + math.ceil(clause_len / pieces)
        chunks.append(words[i:cut])
        i = cut
    # Never strand a one-word tail.
    if len(chunks) >= 2 and len(chunks[-1]) < _MIN_CUE_WORDS:
        tail = chunks.pop()
        if len(chunks[-1]) + len(tail) <= max_words + 1:
            chunks[-1] = chunks[-1] + tail
        else:
            merged = chunks[-1] + tail
            half = len(merged) // 2
            chunks[-1] = merged[:half]
            chunks.append(merged[half:])
    if len(chunks) > max_chunks:
        return None
    return chunks


_SENTENCE_END = (".", "!", "?", "\u2026")


def _ends_sentence(word: str) -> bool:
    return word.rstrip(_CLOSERS).endswith(_SENTENCE_END)


def _reattach_stranded_sentence_tails(
    segments: list[CaptionSegment], *, max_words: int,
) -> list[CaptionSegment]:
    """Move a sentence tail Whisper split onto the NEXT segment back where it belongs.

    Clause-aware cutting works inside one ASR segment, but Whisper sometimes
    breaks a segment mid-sentence (2026-09-22, poindexter#1070)::

        22.20-27.62 '... rather than just producing lengthy'
        27.62-35.30 'articles. Instant value wins, ...'

    The cutter cannot end a cue on the lone ``articles.`` (a one-word cue is
    folded), so the burned cues read "than just producing lengthy" /
    "articles. Instant value wins,". When segment *k* ends mid-sentence and
    segment *k+1* opens with a sentence end within ``_MIN_CUE_WORDS`` words,
    those words move to *k* — unless *k*'s last cue would then pass
    ``max_words + 1``, or the two belong to different speakers. The boundary
    between the two windows moves by the tail's share of *k+1*'s text so the
    interpolated timing stays honest; the word-level retime pass re-anchors it
    anyway when timestamps exist.
    """
    out = list(segments)
    absorbed: set[int] = set()
    for k in range(len(out) - 1):
        prev, nxt = out[k], out[k + 1]
        prev_words = (prev.text or "").split()
        next_words = (nxt.text or "").split()
        if not prev_words or not next_words or _ends_sentence(prev_words[-1]):
            continue
        if getattr(prev, "speaker", None) != getattr(nxt, "speaker", None):
            continue
        tail_len = next(
            (i + 1 for i, w in enumerate(next_words[:_MIN_CUE_WORDS]) if _ends_sentence(w)),
            0,
        )
        if not tail_len:
            continue
        merged = prev_words + next_words[:tail_len]
        last_cue = (
            _cut_on_clauses(merged, max_words=max_words, max_chunks=len(merged)) or [merged]
        )[-1]
        if len(merged) > max_words and len(last_cue) > max_words + 1:
            continue
        rest = next_words[tail_len:]
        if not rest:
            out[k] = replace(prev, text=" ".join(merged), end_s=nxt.end_s)
            out[k + 1] = replace(nxt, text="")
            absorbed.add(k + 1)
            continue
        weight = lambda ws: sum(len(w) + 1 for w in ws)  # noqa: E731
        span = max(0.0, float(nxt.end_s) - float(nxt.start_s))
        shift = span * weight(next_words[:tail_len]) / (weight(next_words) or 1)
        boundary = float(nxt.start_s) + shift
        out[k] = replace(prev, text=" ".join(merged), end_s=boundary)
        out[k + 1] = replace(nxt, text=" ".join(rest), start_s=boundary)
    return [seg for i, seg in enumerate(out) if i not in absorbed]


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

    segments = _reattach_stranded_sentence_tails(segments, max_words=max_words)
    out: list[CaptionSegment] = []
    for seg in segments:
        words = (seg.text or "").split()
        duration = max(0.0, float(seg.end_s) - float(seg.start_s))
        n_chunks = math.ceil(len(words) / max_words) if words else 1
        # The only legitimate ceiling on cue COUNT is the seconds floor. The
        # balanced count is a target for the fallback, not a cap — clause cuts
        # make shorter cues, so they routinely need more of them.
        cue_cap = (
            max(1, int(duration / min_cue_seconds))
            if (min_cue_seconds > 0 and duration > 0) else len(words)
        )
        n_chunks = min(n_chunks, cue_cap)
        if n_chunks <= 1:
            out.append(seg)
            continue

        chunks = _cut_on_clauses(words, max_words=max_words, max_chunks=cue_cap)
        if chunks is None:
            # Balanced sizes: base words per chunk, the first ``rem`` get one more.
            base, rem = divmod(len(words), n_chunks)
            chunks = []
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
