"""Unit tests for the sidecar chunk-joiner (numpy-only).

`audio_join` lives under `scripts/tts_sidecars/` (it ships into the slim sidecar
images, so it can't import from the app package). Loaded by file path, same as
the sibling chunker test.

The bug it fixes: Chatterbox generates each sentence-chunk independently and
each carries its own unpredictable edge silence, so raw-concatenating with a
fixed gap produced `tail + gap + head`. Two shipped episodes measured gaps up
to 3.46s where only 0.25s was ever inserted, on ~40% of boundaries in both.
"""

from __future__ import annotations

import importlib.util
from pathlib import Path

import numpy as np
import pytest

_MODULE = Path(__file__).parents[6] / "scripts" / "tts_sidecars" / "audio_join.py"


def _load():
    if not _MODULE.exists():
        pytest.skip(f"audio_join not present at {_MODULE}")
    spec = importlib.util.spec_from_file_location("sidecar_audio_join", _MODULE)
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


SR = 24000


def _chunk(*, lead_s: float, speech_s: float, tail_s: float) -> np.ndarray:
    """A fake generation: silence, then speech, then silence."""
    lead = np.zeros(int(SR * lead_s), dtype=np.float32)
    speech = np.full(int(SR * speech_s), 0.5, dtype=np.float32)
    tail = np.zeros(int(SR * tail_s), dtype=np.float32)
    return np.concatenate([lead, speech, tail])


@pytest.mark.unit
class TestTrimEdgeSilence:
    def test_strips_leading_and_trailing_silence(self):
        m = _load()
        wav = _chunk(lead_s=0.8, speech_s=1.0, tail_s=1.4)
        out = m.trim_edge_silence(wav, SR)
        # 1.0s of speech plus the 30ms keep-margin on each side.
        assert 1.0 * SR <= out.size <= int(1.07 * SR)

    def test_keeps_a_margin_so_consonants_are_not_clipped(self):
        m = _load()
        wav = _chunk(lead_s=0.5, speech_s=0.5, tail_s=0.5)
        out = m.trim_edge_silence(wav, SR)
        assert out.size > int(0.5 * SR)  # strictly more than the speech alone

    def test_all_silence_is_returned_unchanged(self):
        """A dead chunk is a generation failure worth hearing, not something to
        silently collapse to zero samples."""
        m = _load()
        wav = np.zeros(SR, dtype=np.float32)
        assert m.trim_edge_silence(wav, SR).size == SR

    def test_empty_input_round_trips(self):
        m = _load()
        assert m.trim_edge_silence(np.zeros(0, dtype=np.float32), SR).size == 0

    def test_quiet_chunk_is_not_over_trimmed(self):
        """The floor is relative to the chunk's own peak — an absolute floor
        would eat a quiet generation entirely."""
        m = _load()
        quiet = _chunk(lead_s=0.3, speech_s=1.0, tail_s=0.3) * 0.01
        out = m.trim_edge_silence(quiet, SR)
        assert 1.0 * SR <= out.size <= int(1.07 * SR)


@pytest.mark.unit
class TestJoinSegments:
    def test_boundary_is_exactly_the_requested_gap(self):
        """The whole point: gap = what was asked for, not tail+gap+head."""
        m = _load()
        segs = [_chunk(lead_s=0.6, speech_s=1.0, tail_s=1.5),
                _chunk(lead_s=0.9, speech_s=1.0, tail_s=1.2)]
        out = m.join_segments(segs, SR, gap_seconds=0.25)

        # Find the silent run between the two speech bursts.
        loud = np.flatnonzero(np.abs(out) > 0.1)
        gap_len = 0
        for a, b in zip(loud[:-1], loud[1:], strict=True):
            gap_len = max(gap_len, int(b - a - 1))
        measured = gap_len / SR
        # 0.25s gap + the two 30ms keep-margins that bracket it.
        assert 0.25 <= measured <= 0.32, f"boundary was {measured:.3f}s"

    def test_untrimmed_reproduces_the_old_runaway_boundary(self):
        """Regression witness: trim=False is the shipped behaviour that
        produced multi-second dead air."""
        m = _load()
        segs = [_chunk(lead_s=0.6, speech_s=1.0, tail_s=1.5),
                _chunk(lead_s=0.9, speech_s=1.0, tail_s=1.2)]
        out = m.join_segments(segs, SR, gap_seconds=0.25, trim=False)
        loud = np.flatnonzero(np.abs(out) > 0.1)
        gap_len = max(int(b - a - 1) for a, b in zip(loud[:-1], loud[1:], strict=True))
        # 1.5 tail + 0.25 gap + 0.9 head = 2.65s, an order above the intent.
        assert gap_len / SR > 2.5

    def test_single_segment_gets_no_gap(self):
        m = _load()
        seg = _chunk(lead_s=0.4, speech_s=1.0, tail_s=0.4)
        out = m.join_segments([seg], SR, gap_seconds=0.25)
        assert 1.0 * SR <= out.size <= int(1.07 * SR)

    def test_zero_gap_butts_chunks_together(self):
        m = _load()
        segs = [_chunk(lead_s=0.2, speech_s=0.5, tail_s=0.2) for _ in range(3)]
        out = m.join_segments(segs, SR, gap_seconds=0.0)
        assert out.size <= int(3 * 0.57 * SR)

    def test_empty_input_yields_one_sample(self):
        """Matches the sidecar's prior fallback so the encoder always has input."""
        m = _load()
        assert m.join_segments([], SR, gap_seconds=0.25).size == 1

    def test_skips_empty_segments(self):
        m = _load()
        segs = [_chunk(lead_s=0.1, speech_s=0.5, tail_s=0.1),
                np.zeros(0, dtype=np.float32),
                _chunk(lead_s=0.1, speech_s=0.5, tail_s=0.1)]
        out = m.join_segments(segs, SR, gap_seconds=0.25)
        loud = np.flatnonzero(np.abs(out) > 0.1)
        gaps = [int(b - a - 1) for a, b in zip(loud[:-1], loud[1:], strict=True) if b - a > 1]
        assert len(gaps) == 1  # one boundary, not two

    def test_output_is_float32(self):
        m = _load()
        segs = [_chunk(lead_s=0.1, speech_s=0.3, tail_s=0.1) for _ in range(2)]
        assert m.join_segments(segs, SR, gap_seconds=0.25).dtype == np.float32

    def test_gap_scales_with_the_setting(self):
        """chunk_gap_seconds is the operator's pacing dial once trimming makes
        the boundary deterministic."""
        m = _load()
        segs = [_chunk(lead_s=0.5, speech_s=0.5, tail_s=0.5) for _ in range(2)]
        sizes = {
            g: m.join_segments(segs, SR, gap_seconds=g).size
            for g in (0.1, 0.5, 1.0)
        }
        assert sizes[0.1] < sizes[0.5] < sizes[1.0]
        assert sizes[1.0] - sizes[0.1] == pytest.approx(0.9 * SR, abs=2)
