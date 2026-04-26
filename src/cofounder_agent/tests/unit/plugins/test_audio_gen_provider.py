"""Unit tests for the AudioGenProvider Protocol + AudioGenResult dataclass.

The Protocol itself is structural; tests here validate the dataclass
shape, the runtime_checkable Protocol's isinstance() behavior, and the
type alias surface from ``plugins/__init__.py``. Concrete-provider
behavior is tested in
``tests/unit/services/test_audio_gen_providers_stable_audio_open.py``.
"""

from __future__ import annotations

import pytest

from plugins import AudioGenProvider, AudioGenResult, AudioKind
from plugins.audio_gen_provider import AudioGenProvider as DirectAudioGenProvider


@pytest.mark.unit
class TestAudioGenResult:
    def test_minimal_with_bytes(self):
        r = AudioGenResult(audio_bytes=b"fake-audio")
        assert r.audio_bytes == b"fake-audio"
        assert r.file_path == ""
        assert r.kind == "ambient"
        assert r.format == "wav"

    def test_minimal_with_file_path(self):
        r = AudioGenResult(file_path="/tmp/x.wav")
        assert r.file_path == "/tmp/x.wav"
        assert r.audio_bytes is None

    def test_neither_bytes_nor_path_raises(self):
        with pytest.raises(ValueError):
            AudioGenResult()

    def test_full_metadata(self):
        r = AudioGenResult(
            file_path="/tmp/x.wav",
            duration_s=5.0,
            sample_rate=44100,
            kind="intro",
            format="wav",
            prompt="energetic sting",
            source="stable-audio-open-1.0",
            metadata={"license": "stability-ai-community"},
        )
        assert r.kind == "intro"
        assert r.duration_s == 5.0
        assert r.metadata["license"] == "stability-ai-community"

    def test_to_dict_roundtrip(self):
        r = AudioGenResult(
            audio_bytes=b"abcd",
            duration_s=1.0,
            sample_rate=22050,
            kind="sfx",
            source="test",
        )
        d = r.to_dict()
        # Bytes are summarized via length to keep the dict log-safe
        assert d["audio_bytes_len"] == 4
        assert d["kind"] == "sfx"
        assert d["sample_rate"] == 22050

    def test_kind_literal_supports_all_four(self):
        # Sanity: the four production kinds round-trip via the dataclass.
        for kind in ("ambient", "sfx", "intro", "outro"):
            r = AudioGenResult(file_path="/tmp/x", kind=kind)
            assert r.kind == kind


@pytest.mark.unit
class TestProtocolSurface:
    def test_protocol_is_runtime_checkable(self):
        class _Stub:
            name = "stub"
            kinds: tuple[AudioKind, ...] = ("ambient",)

            async def generate(self, prompt, kind, config):
                return None

        assert isinstance(_Stub(), AudioGenProvider)

    def test_missing_attributes_fail_isinstance(self):
        class _NotAProvider:
            pass

        # No name, no kinds, no generate — fails the structural check.
        assert not isinstance(_NotAProvider(), AudioGenProvider)

    def test_module_import_matches_package_export(self):
        # plugins/__init__.py re-export and direct import must point at
        # the same Protocol object.
        assert AudioGenProvider is DirectAudioGenProvider
