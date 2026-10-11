"""Mid-sentence name casing for Chatterbox, and the two in-sidecar pause fixes end to end.

``text_casing`` ships into the slim chatterbox sidecar image, so it is loaded by
file path (same pattern as ``test_truncation_guard``). Chatterbox's vocabulary
has no multi-letter capital pieces, so a mid-sentence name is read one capital
at a time and the engine pauses before it: 15.7% of renders at 0.4s or more
before a name, 5.3% after folding, other boundaries 0.6-0.7% both ways.
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path
from unittest.mock import MagicMock

import numpy as np
import pytest

_DIR = Path(__file__).parents[6] / "scripts" / "tts_sidecars"


def _load(name: str):
    path = _DIR / f"{name}.py"
    if not path.exists():
        pytest.skip(f"sidecar module not present at {path}")
    spec = importlib.util.spec_from_file_location(f"{name}_case_ut", path)
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


@pytest.fixture
def tc():
    return _load("text_casing")


@pytest.mark.unit
class TestFoldNameCase:
    def test_mid_sentence_names_are_lowercased(self, tc):
        out, n = tc.fold_name_case("Benchmarks score Alibaba above the Steam Deck.")
        assert out == "Benchmarks score alibaba above the steam deck."
        assert n == 3

    def test_camel_case_names_split_into_words(self, tc):
        out, _ = tc.fold_name_case("It says DeepSeek beat SanDisk.")
        assert out == "It says deep seek beat san disk."

    def test_mc_and_mac_names_stay_one_word(self, tc):
        out, _ = tc.fold_name_case("As McDonald and MacKenzie said.")
        assert out == "As mcdonald and mackenzie said."

    def test_sentence_starts_and_pronoun_i_are_kept(self, tc):
        text = "Alibaba won. DeepSeek replied! I think I'm right? Yes\n\nNew Paragraph"
        out, _ = tc.fold_name_case(text)
        # A paragraph break starts a sentence even without a full stop before it.
        assert out == "Alibaba won. DeepSeek replied! I think I'm right? Yes\n\nNew paragraph"

    def test_acronyms_mixed_runs_and_digit_tokens_are_untouched(self, tc):
        """Single capitals are how the model spells letters, which an acronym needs."""
        text = "We ran the ASUS ROG RTX 5090 with NVMe drives, OpenAI's GPU and the SN770M."
        out, n = tc.fold_name_case(text)
        assert out == text and n == 0

    def test_keep_case_words_are_never_folded(self, tc):
        """'August' the month and 'august' the adjective are different words."""
        out, _ = tc.fold_name_case("It shipped in August with Polish support.")
        assert out == "It shipped in August with Polish support."
        out, _ = tc.fold_name_case("It shipped in August.", keep_case=())
        assert out == "It shipped in august."

    def test_punctuation_and_possessives_survive(self, tc):
        out, _ = tc.fold_name_case('He loved "Baldur\'s" and (Kingston), then Steam\'s store.')
        assert out == 'He loved "baldur\'s" and (kingston), then steam\'s store.'

    def test_whitespace_is_preserved_exactly(self, tc):
        text = "One  two\tThree.\n\nFour Five"
        out, _ = tc.fold_name_case(text)
        assert out == "One  two\tthree.\n\nFour five"

    def test_empty_input(self, tc):
        assert tc.fold_name_case("") == ("", 0)


# --- the sidecar request path ------------------------------------------------------

SR = 1000


class _FakeWav:
    def __init__(self, arr):
        self._arr = arr

    def squeeze(self, _):
        return self

    def detach(self):
        return self

    def cpu(self):
        return self

    def numpy(self):
        return self._arr


class _FakeModel:
    """1 s of speech, a 3 s dead-air pause, 1 s of speech — for every call."""

    sr = SR

    def __init__(self):
        self.texts: list[str] = []

    def generate(self, text, **_):
        self.texts.append(text)
        speech = np.full(SR, 0.5, dtype=np.float32)
        return _FakeWav(np.concatenate([speech, np.zeros(3 * SR, dtype=np.float32), speech]))


@pytest.fixture
def server(monkeypatch):
    path = _DIR / "chatterbox_server.py"
    if not path.exists():
        pytest.skip(f"sidecar not present at {path}")
    monkeypatch.setitem(sys.modules, "soundfile", MagicMock())
    monkeypatch.syspath_prepend(str(_DIR))
    spec = importlib.util.spec_from_file_location("chatterbox_server_case_ut", path)
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    model = _FakeModel()
    encoded: list[np.ndarray] = []
    monkeypatch.setattr(mod, "_get_model", lambda: model)
    monkeypatch.setattr(mod, "_encode", lambda samples, sr, fmt: encoded.append(samples) or b"x")
    mod._test_model, mod._test_encoded = model, encoded
    return mod


def _request(server, **kw):
    kw.setdefault("truncation_max_rate_ratio", 0)
    req = server.SpeechRequest(input="Benchmarks say DeepSeek beat Alibaba in August.", **kw)
    server._synthesize(req, None)
    return server._test_model.texts, server._test_encoded[-1]


@pytest.mark.unit
def test_the_model_receives_folded_names_by_default(server):
    texts, _ = _request(server)
    assert texts == ["Benchmarks say deep seek beat alibaba in August."]


@pytest.mark.unit
def test_fold_can_be_switched_off_and_keep_list_overridden(server):
    texts, _ = _request(server, fold_name_case=False)
    assert texts[-1] == "Benchmarks say DeepSeek beat Alibaba in August."
    texts, _ = _request(server, keep_case_words=[])
    assert texts[-1] == "Benchmarks say deep seek beat alibaba in august."


@pytest.mark.unit
def test_the_dead_air_inside_a_chunk_is_clamped_before_encoding(server):
    _, samples = _request(server)
    # 2 s of speech + the default 0.55 s cap (less the 10 ms splice fade).
    assert samples.size / SR == pytest.approx(2.55, abs=0.03)
    _, samples = _request(server, max_internal_silence_seconds=0)
    assert samples.size / SR == pytest.approx(5.0, abs=0.03)
