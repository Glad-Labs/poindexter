"""The TTS early-stop guard: a chunk with too little audio for its text lost text.

``truncation_guard`` ships into the slim chatterbox sidecar image, so it is
loaded by file path (same pattern as ``test_text_chunking``). The repair loop in
``chatterbox_server`` is tested through the server module with a fake generator.
"""

from __future__ import annotations

import importlib.util
import logging
import sys
from pathlib import Path
from unittest.mock import MagicMock

import numpy as np
import pytest

_DIR = Path(__file__).parents[6] / "scripts" / "tts_sidecars"
SR = 1000  # samples per second: 1 s of audio = 1000 samples

# The chunk a 2026-10-06 narration lost two sentences of.
LOST = (
    "This wasn't routine paperwork, it was an unveiling of internal documents previously "
    "hidden away under seal. The contents? A highlight reel no one at OpenAI wanted public. "
    "Two elements stand out starkly from this filing."
)
FIRST_SENTENCE = LOST.split(". The contents?")[0] + "."
# Production median measured over 41 chunks: 16.4 chars/s.
RATE = 16.4


def _load(name: str):
    path = _DIR / f"{name}.py"
    if not path.exists():
        pytest.skip(f"sidecar module not present at {path}")
    spec = importlib.util.spec_from_file_location(f"{name}_ut", path)
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


@pytest.fixture
def tg():
    return _load("truncation_guard")


def _secs(text: str, rate: float = RATE) -> float:
    return len(text) / rate


def test_the_2026_10_06_chunk_is_flagged(tg):
    # Chatterbox voiced only the first sentence of the chunk.
    spoken = _secs(FIRST_SENTENCE)
    assert tg.is_truncated(LOST, spoken, RATE)


def test_every_measured_production_rate_passes(tg):
    # 0.73x-1.23x of the median was the full spread across 41 real chunks.
    for ratio in (0.73, 0.9, 1.0, 1.1, 1.23, 1.45):
        assert not tg.is_truncated(LOST, _secs(LOST, RATE * ratio), RATE), ratio


def test_short_chunks_are_not_judged(tg):
    assert not tg.is_truncated("Thanks.", 0.05, RATE)


def test_zero_ratio_disables(tg):
    assert not tg.is_truncated(LOST, 0.5, RATE, max_ratio=0)


def test_reference_is_the_request_median(tg):
    texts = ["x" * 120, "y" * 150, "z" * 200, LOST]
    secs = [120 / 15.0, 150 / 16.0, 200 / 17.0, _secs(FIRST_SENTENCE)]
    assert tg.reference_rate(texts, secs) == pytest.approx(16.5, abs=0.6)


def test_too_few_chunks_falls_back_to_the_production_rate(tg):
    assert tg.reference_rate([LOST], [_secs(FIRST_SENTENCE)]) == tg.FALLBACK_CHARS_PER_SECOND
    # A one-chunk request is still judged: against the fallback rate.
    assert tg.is_truncated(LOST, _secs(FIRST_SENTENCE), tg.FALLBACK_CHARS_PER_SECOND)


def test_split_sentences(tg):
    assert tg.split_sentences(LOST) == [
        FIRST_SENTENCE,
        "The contents?",
        "A highlight reel no one at OpenAI wanted public.",
        "Two elements stand out starkly from this filing.",
    ]


# --- the repair loop in the sidecar -------------------------------------------------


@pytest.fixture
def server(monkeypatch):
    path = _DIR / "chatterbox_server.py"
    if not path.exists():
        pytest.skip(f"sidecar not present at {path}")
    monkeypatch.setitem(sys.modules, "soundfile", MagicMock())
    monkeypatch.syspath_prepend(str(_DIR))
    spec = importlib.util.spec_from_file_location("chatterbox_server_trunc_ut", path)
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _audio(text: str) -> np.ndarray:
    """Fully-voiced audio for ``text`` at the production rate (no edge silence)."""
    return np.full(int(_secs(text) * SR), 0.5, dtype=np.float32)


OTHERS = [
    "First, it alleges that OpenAI obtained books from what the Authors Guild describes "
    "as a sketchy Russian website.",
    "This is code for shadow libraries, illegal aggregators of pirated book scans and "
    "archival material that have been central to AI training data lawsuits.",
    "But more telling than the source itself are the internal communications unearthed.",
]


def _repair(server, chunks, segments, generate, **kw):
    kw.setdefault("max_ratio", 1.5)
    kw.setdefault("retries", 2)
    return server._repair_truncated_chunks(
        chunks, segments, SR, generate, gap_seconds=0.0, trim=False, **kw,
    )


def test_a_truncated_chunk_is_regenerated_sentence_by_sentence(server):
    chunks = [*OTHERS, LOST]
    segments = [_audio(c) for c in OTHERS] + [_audio(FIRST_SENTENCE)]
    calls: list[str] = []

    def generate(text):
        calls.append(text)
        return _audio(text)

    out = _repair(server, chunks, segments, generate)
    assert calls == server.split_sentences(LOST)  # one retry, one call per sentence
    assert out[-1].size / SR == pytest.approx(_secs(LOST), rel=0.05)
    assert all(a is b for a, b in zip(out[:3], segments[:3], strict=True))  # healthy chunks untouched


def test_a_healthy_request_is_never_regenerated(server):
    chunks = [*OTHERS, LOST]

    def generate(text):
        raise AssertionError("no chunk should be regenerated")

    out = _repair(server, chunks, [_audio(c) for c in chunks], generate)
    assert len(out) == 4


def test_a_chunk_that_stays_short_keeps_its_best_take_and_warns(server, caplog):
    caplog.set_level(logging.WARNING, logger="chatterbox-server")
    chunks = [*OTHERS, LOST]
    segments = [_audio(c) for c in OTHERS] + [_audio("x" * 20)]   # ~1.2 s
    # Each retry generates the 4 sentences; every take stays far too short.
    per_call = iter([0.4] * 4 + [0.3] * 4)                        # 1.6 s, then 1.2 s

    def generate(text):
        return np.full(int(next(per_call) * SR), 0.5, dtype=np.float32)

    out = _repair(server, chunks, segments, generate)
    assert out[-1].size == int(0.4 * SR) * 4  # the longest take wins
    assert "STILL short" in caplog.text


@pytest.mark.parametrize("kw", [{"retries": 0}, {"max_ratio": 0}])
def test_the_guard_can_be_switched_off(server, kw):
    chunks = [*OTHERS, LOST]
    segments = [_audio(c) for c in OTHERS] + [_audio(FIRST_SENTENCE)]

    def generate(text):
        raise AssertionError("disabled guard regenerated a chunk")

    assert _repair(server, chunks, segments, generate, **kw) == segments
