"""Unit tests for the ``TTSProvider`` Protocol + registry wiring (GH-122).

Covers:

- ``TTSResult`` dataclass shape (defaults, ``to_dict``).
- ``TTSProvider`` Protocol runtime-checkability.
- ``get_tts_providers()`` discovers entry-point-registered providers.
- ``ENTRY_POINT_GROUPS`` carries the canonical group name.
"""

from __future__ import annotations

from importlib.metadata import EntryPoint
from pathlib import Path
from typing import Any

import pytest

from plugins import TTSProvider, TTSResult, get_tts_providers
from plugins.registry import ENTRY_POINT_GROUPS, clear_registry_cache


class _FakeTTS:
    """Minimal TTSProvider-shaped test double."""

    name = "fake_tts"
    sample_rate_hz = 24000
    default_format = "wav"

    async def synthesize(
        self,
        text: str,
        output_path: Path,
        *,
        voice: str | None = None,
        config: dict[str, Any] | None = None,
    ) -> TTSResult:
        return TTSResult(
            audio_path=output_path,
            duration_seconds=len(text.split()) // 2,
            voice=voice or "fake-voice",
            sample_rate=self.sample_rate_hz,
            audio_format=self.default_format,
            file_size_bytes=42,
        )


class _EntryPointWithFixedLoad:
    """Subset of ``EntryPoint`` returning a pre-supplied target on load.

    Mirrors the helper in ``test_registry.py`` — duck-typed so it
    satisfies the iteration contract of ``plugins.registry._load_group``.
    """

    def __init__(self, ep: EntryPoint, target: type) -> None:
        self._ep = ep
        self._target = target

    @property
    def name(self) -> str:
        return self._ep.name

    @property
    def group(self) -> str:
        return self._ep.group

    def load(self) -> type:
        return self._target


def _make_ep(name: str, group: str, target: type) -> _EntryPointWithFixedLoad:
    ep = EntryPoint(name=name, value=f"test_module:{target.__name__}", group=group)
    return _EntryPointWithFixedLoad(ep, target)


@pytest.fixture(autouse=True)
def _reset_registry_cache():
    clear_registry_cache()
    yield
    clear_registry_cache()


class TestTTSResult:
    def test_defaults(self):
        r = TTSResult()
        assert r.audio_path is None
        assert r.audio_bytes == b""
        assert r.duration_seconds == 0
        assert r.voice == ""
        assert r.sample_rate == 24000
        assert r.audio_format == "mp3"
        assert r.file_size_bytes == 0
        assert r.metadata == {}

    def test_to_dict_roundtrips_path(self, tmp_path):
        out = tmp_path / "ep.wav"
        r = TTSResult(
            audio_path=out,
            duration_seconds=120,
            voice="af_heart",
            sample_rate=24000,
            audio_format="wav",
            file_size_bytes=1024,
            metadata={"engine": "kokoro-82M"},
        )
        d = r.to_dict()
        assert d["audio_path"] == str(out)
        assert d["audio_bytes_len"] == 0
        assert d["duration_seconds"] == 120
        assert d["voice"] == "af_heart"
        assert d["sample_rate"] == 24000
        assert d["audio_format"] == "wav"
        assert d["file_size_bytes"] == 1024
        assert d["metadata"] == {"engine": "kokoro-82M"}

    def test_to_dict_handles_bytes_only(self):
        r = TTSResult(audio_bytes=b"\x00\x01\x02", audio_format="opus")
        d = r.to_dict()
        assert d["audio_path"] is None
        assert d["audio_bytes_len"] == 3


class TestTTSProviderProtocol:
    def test_runtime_checkable_with_fake(self):
        """A class with the right shape is recognized as a TTSProvider."""
        assert isinstance(_FakeTTS(), TTSProvider)

    def test_missing_method_fails_isinstance(self):
        class _NoSynthesize:
            name = "broken"
            sample_rate_hz = 24000
            default_format = "wav"

        # runtime_checkable Protocol checks *attribute* presence, so
        # we explicitly verify the contract requires ``synthesize``.
        assert not isinstance(_NoSynthesize(), TTSProvider)


class TestRegistryGroup:
    def test_entry_point_group_registered(self):
        """The canonical entry_point group name is present in registry."""
        assert ENTRY_POINT_GROUPS["tts_providers"] == "poindexter.tts_providers"

    def test_get_tts_providers_discovers_entry_point(self, monkeypatch):
        def fake_entry_points(group: str | None = None):
            if group == "poindexter.tts_providers":
                return [_make_ep("fake_tts", "poindexter.tts_providers", _FakeTTS)]
            return []

        monkeypatch.setattr("plugins.registry.entry_points", fake_entry_points)
        clear_registry_cache()

        providers = get_tts_providers()
        assert len(providers) == 1
        assert providers[0].name == "fake_tts"
        assert providers[0].sample_rate_hz == 24000
        assert providers[0].default_format == "wav"

    def test_get_tts_providers_isolation_from_other_groups(self, monkeypatch):
        """A provider under another group does not leak into TTS results."""
        def fake_entry_points(group: str | None = None):
            if group == "poindexter.image_providers":
                return [_make_ep("not_tts", "poindexter.image_providers", _FakeTTS)]
            return []

        monkeypatch.setattr("plugins.registry.entry_points", fake_entry_points)
        clear_registry_cache()

        assert get_tts_providers() == []
