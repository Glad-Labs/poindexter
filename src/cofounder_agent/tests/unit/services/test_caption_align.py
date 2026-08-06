"""Forced script→ASR-timing caption alignment (2026-08-03).

Burned captions read "PHY 4" / "gait" because they were Whisper's phonetic
transcription of the TTS audio. The aligner rewrites segment TEXT from the
known script while keeping ASR timings; these tests pin the correction of
exactly that error class plus the fail-open quality gate.
"""

from __future__ import annotations

from plugins.caption_provider import CaptionSegment
from services.caption_align import align_script_to_segments, segments_to_srt


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
