"""Forced script→ASR-timing caption alignment (2026-08-03).

Burned captions read "PHY 4" / "gait" because they were Whisper's phonetic
transcription of the TTS audio. The aligner rewrites segment TEXT from the
known script while keeping ASR timings; these tests pin the correction of
exactly that error class plus the fail-open quality gate.
"""

from __future__ import annotations

from poindexter.plugins.caption_provider import CaptionSegment
from poindexter.services.caption_align import (
    align_script_to_segments,
    retime_cues_to_words,
    segments_to_srt,
    split_segments_for_display,
)


def _seg(start: float, end: float, text: str) -> CaptionSegment:
    return CaptionSegment(start_s=start, end_s=end, text=text)


class TestAlignment:
    def test_homophones_and_phonetic_names_corrected(self):
        # The operator-observed shape: ASR wrote "PHY 4" and "gait".
        segments = [
            _seg(0.0, 3.0, "the PHY 4 model runs the review"),
            _seg(3.0, 6.0, "before the approval gait opens"),
        ]
        script = "The Phi-4 model runs the review before the approval gate opens."
        out, fraction = align_script_to_segments(segments, script)
        assert fraction > 0.7
        joined = " ".join(s.text for s in out)
        assert "Phi-4" in joined
        assert "gate" in joined
        assert "PHY" not in joined
        assert "gait" not in joined
        # Timings untouched.
        assert out[0].start_s == 0.0 and out[-1].end_s == 6.0

    def test_every_script_token_appears_once_in_order(self):
        segments = [
            _seg(0.0, 2.0, "five tech giants are carrying"),
            _seg(2.0, 4.0, "one point six five trillion dollars"),
            _seg(4.0, 6.0, "in hidden debt to fuel the boom"),
        ]
        script = (
            "Five tech giants are carrying one point six five trillion "
            "dollars in hidden debt to fuel the boom"
        )
        out, fraction = align_script_to_segments(segments, script)
        assert fraction > 0.9
        assert " ".join(s.text for s in out).split() == script.split()

    def test_asr_insertion_absorbed(self):
        # Whisper hallucinated an extra token; script tail must not shift off.
        segments = [
            _seg(0.0, 2.0, "the pipeline uh runs nightly"),
            _seg(2.0, 4.0, "and ships every post"),
        ]
        script = "The pipeline runs nightly and ships every post."
        out, _ = align_script_to_segments(segments, script)
        assert " ".join(s.text for s in out).split() == script.split()

    def test_low_match_returns_low_fraction(self):
        segments = [_seg(0.0, 2.0, "completely unrelated words here now")]
        script = "The quarterly numbers tell a different story entirely."
        out, fraction = align_script_to_segments(segments, script)
        assert fraction < 0.5

    def test_degenerate_inputs_untouched(self):
        segs = [_seg(0.0, 1.0, "hello world")]
        out, fraction = align_script_to_segments(segs, "")
        assert out == segs and fraction == 0.0
        out2, fraction2 = align_script_to_segments([], "some script")
        assert out2 == [] and fraction2 == 0.0

    def test_script_tail_never_dropped(self):
        # ASR truncated early; the last segment still carries the script tail.
        segments = [
            _seg(0.0, 2.0, "the gap should worry"),
        ]
        script = "The gap should worry anyone renting a GPU today."
        out, _ = align_script_to_segments(segments, script)
        assert out[-1].text.endswith("GPU today.")


class TestSrt:
    def test_srt_format(self):
        srt = segments_to_srt([_seg(0.0, 2.5, "Hello there"), _seg(2.5, 5.0, "Phi-4 wins")])
        assert "1\n00:00:00,000 --> 00:00:02,500\nHello there" in srt
        assert "2\n00:00:02,500 --> 00:00:05,000\nPhi-4 wins" in srt

    def test_empty_returns_empty(self):
        assert segments_to_srt([]) == ""


class TestSplitSegmentsForDisplay:
    """Display-cue chunking (2026-08-24 giant-caption report).

    Whisper segments are sentence-sized; burned whole into a 9:16 frame they
    wrap into a frame-filling text wall. The splitter re-cuts them into short
    cues with timings interpolated inside the parent segment.
    """

    def test_short_segment_untouched(self):
        segs = [_seg(0.0, 2.0, "four words stay put")]
        assert split_segments_for_display(segs, max_words=5) == segs

    def test_long_segment_split_balanced(self):
        # 13 words at max 5 → 3 chunks sized 5/4/4 — never an orphan tail.
        text = "one two three four five six seven eight nine ten eleven twelve thirteen"
        out = split_segments_for_display([_seg(0.0, 6.5, text)], max_words=5)
        sizes = [len(c.text.split()) for c in out]
        assert sizes == [5, 4, 4]
        assert " ".join(c.text for c in out) == text

    def test_timings_contiguous_and_exact_tail(self):
        text = "one two three four five six seven eight nine ten eleven twelve thirteen"
        out = split_segments_for_display([_seg(1.0, 7.5, text)], max_words=5)
        assert out[0].start_s == 1.0
        assert out[-1].end_s == 7.5
        for a, b in zip(out, out[1:], strict=False):
            assert a.end_s == b.start_s
            assert a.end_s > a.start_s
        assert out[-1].end_s > out[-1].start_s

    def test_char_weighted_timing(self):
        # Chunk windows track text length: a chunk with much longer words
        # gets a longer window than an equal-word-count short-word chunk.
        text = "hi ok go extraordinarily incomprehensibilities internationalization"
        out = split_segments_for_display([_seg(0.0, 6.0, text)], max_words=3)
        assert len(out) == 2
        short_window = out[0].end_s - out[0].start_s
        long_window = out[1].end_s - out[1].start_s
        assert long_window > short_window

    def test_min_cue_seconds_caps_chunk_count(self):
        # A 1.2s segment can hold at most two 0.6s cues no matter the words.
        text = "one two three four five six seven eight nine ten eleven twelve"
        out = split_segments_for_display(
            [_seg(0.0, 1.2, text)], max_words=3, min_cue_seconds=0.6,
        )
        assert len(out) == 2

    def test_disabled_via_nonpositive_budget(self):
        segs = [_seg(0.0, 6.0, "a very long segment that would otherwise be split")]
        assert split_segments_for_display(segs, max_words=0) is segs

    def test_multiple_segments_processed_independently(self):
        segs = [
            _seg(0.0, 2.0, "short one"),
            _seg(2.0, 8.0, "one two three four five six seven eight nine ten"),
        ]
        out = split_segments_for_display(segs, max_words=5)
        assert out[0] == segs[0]
        assert len(out) == 3  # 1 untouched + 2 chunks
        assert out[1].start_s == 2.0
        assert out[-1].end_s == 8.0

    def test_speaker_and_confidence_carry_through(self):
        seg = CaptionSegment(
            start_s=0.0, end_s=6.0,
            text="one two three four five six seven eight nine ten",
            speaker="narrator", confidence=0.9,
        )
        out = split_segments_for_display([seg], max_words=5)
        assert all(c.speaker == "narrator" and c.confidence == 0.9 for c in out)

    def test_empty_text_segment_untouched(self):
        segs = [_seg(0.0, 2.0, "")]
        assert split_segments_for_display(segs, max_words=5) == segs

    def test_srt_roundtrip_of_split_cues(self):
        text = "one two three four five six seven eight nine ten"
        out = split_segments_for_display([_seg(0.0, 5.0, text)], max_words=5)
        srt = segments_to_srt(out)
        assert "1\n00:00:00,000 --> " in srt
        assert "one two three four five" in srt
        assert "six seven eight nine ten" in srt


class TestRetimeCuesToWords:
    """Word-timestamp retiming (2026-08-26): cue windows snap to real speech
    onsets so the voice never runs ahead of the text."""

    class _W:
        def __init__(self, start, end, text):
            self.start_s, self.end_s, self.text = start, end, text

    def _words(self, spec):
        # spec: [(start, end, "word"), ...]
        return [self._W(*w) for w in spec]

    def test_cues_snap_to_word_onsets_with_lead(self):
        cues = [
            _seg(0.0, 2.5, "hello there world"),
            _seg(2.5, 5.0, "second cue here"),
        ]
        words = self._words([
            (0.4, 0.8, "hello"), (0.8, 1.2, "there"), (1.2, 1.6, "world"),
            (3.0, 3.4, "second"), (3.4, 3.8, "cue"), (3.8, 4.2, "here"),
        ])
        out = retime_cues_to_words(cues, words, lead_s=0.12)
        assert out[0].start_s == 0.28  # 0.4 − 0.12 lead
        assert out[0].end_s == 1.6
        assert out[1].start_s == 2.88  # 3.0 − 0.12 — the interpolated 2.5 lag fixed
        assert out[1].end_s == 4.2

    def test_lead_never_eats_previous_cue(self):
        cues = [_seg(0.0, 1.0, "one"), _seg(1.0, 2.0, "two")]
        words = self._words([(0.5, 1.4, "one"), (1.45, 2.0, "two")])
        out = retime_cues_to_words(cues, words, lead_s=0.5)
        assert out[1].start_s >= out[0].end_s  # clamped to previous end

    def test_homophone_asr_still_anchors(self):
        # Cue text is the clean script ("gate"); ASR word is "gait" — the
        # normalized matcher won't pair those two tokens, but the cue's other
        # words anchor it.
        cues = [_seg(0.0, 3.0, "the gate holds fast")]
        words = self._words([
            (1.0, 1.2, "the"), (1.2, 1.5, "gait"),
            (1.5, 1.9, "holds"), (1.9, 2.3, "fast"),
        ])
        out = retime_cues_to_words(cues, words, lead_s=0.0)
        assert out[0].start_s == 1.0
        assert out[0].end_s == 2.3

    def test_unmatched_cue_keeps_window_monotone(self):
        cues = [
            _seg(0.0, 2.0, "alpha beta"),
            _seg(2.0, 4.0, "completely different text"),
        ]
        words = self._words([(0.5, 1.0, "alpha"), (1.0, 1.5, "beta")])
        out = retime_cues_to_words(cues, words, lead_s=0.1)
        assert out[1].start_s >= out[0].end_s
        assert out[1].end_s > out[1].start_s

    def test_no_words_returns_cues_unchanged(self):
        cues = [_seg(0.0, 2.0, "hello")]
        assert retime_cues_to_words(cues, [], lead_s=0.12) is cues

    def test_minimum_window_enforced(self):
        cues = [_seg(0.0, 5.0, "quick")]
        words = self._words([(1.0, 1.05, "quick")])
        out = retime_cues_to_words(cues, words, lead_s=0.0)
        assert out[0].end_s - out[0].start_s >= 0.3


class TestClauseAwareCuts:
    """Cues end on the writer's punctuation, not on a word count.

    The balanced split produced every caption in the newest short as a
    mid-clause fragment (2026-09-21 review). Aligned segments carry the clean
    script, so its commas and full stops are the right cut points.
    """

    def test_cuts_on_the_comma_not_mid_clause(self):
        text = ("Nobody clicks anymore, so keep them engaged without leaving, "
                "and put the information in the post itself.")
        out = split_segments_for_display([_seg(0.0, 9.0, text)], max_words=6)
        cues = [c.text for c in out]
        assert " ".join(cues) == text
        # Exact decisions, pinned: the first two cuts land on commas; the last
        # clause has no punctuation inside a 6-word window, so it is balanced
        # 4/4 rather than greedy 6/2. Every cue is a whole thought or half of
        # one — never "...themselves, not".
        assert cues == [
            "Nobody clicks anymore,",
            "so keep them engaged without leaving,",
            "and put the information",
            "in the post itself.",
        ]
        assert all(2 <= len(c.split()) <= 6 for c in cues)

    def test_unpunctuated_text_still_splits_balanced(self):
        """The pinned 5/4/4 behaviour is untouched when there is nothing to cut on."""
        text = "one two three four five six seven eight nine ten eleven twelve thirteen"
        out = split_segments_for_display([_seg(0.0, 6.5, text)], max_words=5)
        assert [len(c.text.split()) for c in out] == [5, 4, 4]

    def test_no_orphan_tail_after_a_late_comma(self):
        # a boundary right before the end would leave a 1-word tail — fold it
        text = "alpha beta gamma delta epsilon, zeta"
        out = split_segments_for_display([_seg(0.0, 4.0, text)], max_words=5)
        assert all(len(c.text.split()) >= 2 for c in out), [c.text for c in out]
        assert " ".join(c.text for c in out) == text

    def test_timings_stay_contiguous_with_exact_tail(self):
        text = "First clause here, second clause follows, and the third one ends."
        out = split_segments_for_display([_seg(2.0, 8.0, text)], max_words=4)
        assert out[0].start_s == 2.0 and out[-1].end_s == 8.0
        for a, b in zip(out, out[1:], strict=False):
            assert a.end_s == b.start_s

    def test_min_cue_seconds_cap_falls_back_to_balanced(self):
        # 4 clauses but only room for 2 cues — honouring commas would exceed the cap
        text = "a b, c d, e f, g h"
        out = split_segments_for_display(
            [_seg(0.0, 1.2, text)], max_words=2, min_cue_seconds=0.6,
        )
        assert len(out) == 2
        assert " ".join(c.text for c in out) == text


class TestStrandedSentenceTail:
    """poindexter#1070 — Whisper split a sentence across two segments, so the
    next segment opened with the one-word tail ``articles.`` and the cutter
    (which works per segment) burned "than just producing lengthy" /
    "articles. Instant value wins,". The real 2026-09-22 segments:"""

    SEGS = [
        _seg(22.20, 27.62,
             "focus on crafting valuable, easily extractable insights rather "
             "than just producing lengthy"),
        _seg(27.62, 35.30,
             "articles. Instant value wins, particularly in B2B settings where "
             "decision makers seek quick answers."),
    ]

    def test_the_tail_rejoins_its_sentence(self):
        cues = [c.text for c in split_segments_for_display(self.SEGS, max_words=5)]
        assert any(c.endswith("lengthy articles.") for c in cues), cues
        assert not any(c.startswith("articles.") for c in cues), cues
        assert any(c.startswith("Instant value wins") for c in cues), cues

    def test_every_word_survives_in_order(self):
        before = " ".join(s.text for s in self.SEGS).split()
        after = " ".join(c.text for c in split_segments_for_display(self.SEGS, max_words=5)).split()
        assert after == before

    def test_timing_stays_contiguous_and_monotone(self):
        out = split_segments_for_display(self.SEGS, max_words=5)
        assert out[0].start_s == 22.20 and out[-1].end_s == 35.30
        for a, b in zip(out, out[1:], strict=False):
            assert abs(a.end_s - b.start_s) < 1e-9
            assert a.start_s < a.end_s
        # The boundary moved past 27.62 by the tail's share of segment two.
        lengthy = next(c for c in out if c.text.endswith("lengthy articles."))
        assert 27.62 < lengthy.end_s < 29.0

    def test_a_real_sentence_start_is_not_moved(self):
        """Segment one ENDS a sentence: "Yes." opening segment two is its own
        sentence, not a stranded tail."""
        segs = [
            _seg(0.0, 3.0, "Did the cheap model win the benchmark?"),
            _seg(3.0, 6.0, "Yes. It beat the frontier model on every task."),
        ]
        cues = [c.text for c in split_segments_for_display(segs, max_words=5)]
        assert not any("benchmark? Yes." in c for c in cues)

    def test_different_speakers_are_not_merged(self):
        a = CaptionSegment(start_s=0.0, end_s=3.0, text="the answer is really", speaker="A")
        b = CaptionSegment(start_s=3.0, end_s=6.0, text="simple. Then I said more words here", speaker="B")
        cues = [c.text for c in split_segments_for_display([a, b], max_words=5)]
        assert any(c.startswith("simple.") for c in cues)

    def test_a_whole_tail_segment_is_absorbed(self):
        segs = [_seg(0.0, 3.0, "we shipped it on"), _seg(3.0, 3.5, "Tuesday.")]
        out = split_segments_for_display(segs, max_words=5)
        assert [c.text for c in out] == ["we shipped it on Tuesday."]
        assert out[0].end_s == 3.5
