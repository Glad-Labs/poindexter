"""Unit tests for ``services.tts_providers.edge_tts.EdgeTTSProvider`` (GH-122).

The ``edge-tts`` package is mocked via ``sys.modules`` so the provider's
contract can be exercised without a network call. Real Edge TTS is
covered by integration tests; this file verifies the wrapper preserves
legacy behaviour exactly.
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from plugins.tts_provider import TTSProvider, TTSResult
from services.tts_providers.edge_tts import EdgeTTSProvider


def _make_edge_tts_module(write_bytes: bytes = b"FAKE_MP3", *, raise_exc: Exception | None = None):
    """Build a stand-in for the ``edge_tts`` PyPI package."""

    class FakeCommunicate:
        def __init__(self, script, voice):
            self.script = script
            self.voice = voice

        async def save(self, path):
            if raise_exc is not None:
                raise raise_exc
            Path(path).write_bytes(write_bytes)

    mod = MagicMock()
    mod.Communicate = FakeCommunicate
    return mod


class TestSynthesize:
    @pytest.mark.asyncio
    async def test_writes_mp3_and_returns_result(self, tmp_path):
        out = tmp_path / "ep.mp3"
        with patch.dict("sys.modules", {"edge_tts": _make_edge_tts_module(b"abc123")}):
            result = await EdgeTTSProvider().synthesize(
                "Hello listeners",
                out,
                voice="en-US-AvaMultilingualNeural",
            )

        assert isinstance(result, TTSResult)
        assert result.audio_path == out
        assert out.exists()
        assert out.read_bytes() == b"abc123"
        assert result.voice == "en-US-AvaMultilingualNeural"
        assert result.sample_rate == 24000
        assert result.audio_format == "mp3"
        assert result.file_size_bytes == 6
        assert result.duration_seconds >= 30  # min duration
        assert result.metadata == {"engine": "edge-tts"}

    @pytest.mark.asyncio
    async def test_default_voice_when_none_passed(self, tmp_path):
        with patch.dict("sys.modules", {"edge_tts": _make_edge_tts_module(b"x" * 100)}):
            r = await EdgeTTSProvider().synthesize("Hello", tmp_path / "a.mp3")
        assert r.voice == "en-US-AvaMultilingualNeural"

    @pytest.mark.asyncio
    async def test_config_default_voice_used_when_voice_arg_none(self, tmp_path):
        with patch.dict("sys.modules", {"edge_tts": _make_edge_tts_module(b"x" * 100)}):
            r = await EdgeTTSProvider().synthesize(
                "Hello",
                tmp_path / "a.mp3",
                config={"default_voice": "en-US-AndrewMultilingualNeural"},
            )
        assert r.voice == "en-US-AndrewMultilingualNeural"

    @pytest.mark.asyncio
    async def test_explicit_voice_wins_over_config(self, tmp_path):
        with patch.dict("sys.modules", {"edge_tts": _make_edge_tts_module(b"x" * 100)}):
            r = await EdgeTTSProvider().synthesize(
                "Hello",
                tmp_path / "a.mp3",
                voice="en-US-BrianMultilingualNeural",
                config={"default_voice": "en-US-AndrewMultilingualNeural"},
            )
        assert r.voice == "en-US-BrianMultilingualNeural"

    @pytest.mark.asyncio
    async def test_empty_text_rejected(self, tmp_path):
        with pytest.raises(ValueError):
            await EdgeTTSProvider().synthesize("", tmp_path / "a.mp3")
        with pytest.raises(ValueError):
            await EdgeTTSProvider().synthesize("   \n  ", tmp_path / "a.mp3")

    @pytest.mark.asyncio
    async def test_save_exception_cleans_up_partial(self, tmp_path):
        out = tmp_path / "ep.mp3"
        out.write_bytes(b"partial")
        with patch.dict(
            "sys.modules",
            {"edge_tts": _make_edge_tts_module(raise_exc=RuntimeError("network down"))},
        ):
            with pytest.raises(RuntimeError, match="synthesis failed"):
                await EdgeTTSProvider().synthesize(
                    "hi",
                    out,
                    voice="en-US-AvaMultilingualNeural",
                )
        assert not out.exists()

    @pytest.mark.asyncio
    async def test_empty_file_after_save_raises(self, tmp_path):
        # save() succeeds but writes a zero-byte file
        with patch.dict("sys.modules", {"edge_tts": _make_edge_tts_module(b"")}):
            with pytest.raises(RuntimeError, match="empty file"):
                await EdgeTTSProvider().synthesize(
                    "hi", tmp_path / "ep.mp3", voice="en-US-AvaMultilingualNeural",
                )

    @pytest.mark.asyncio
    async def test_missing_edge_tts_raises_runtime_error(self, tmp_path, monkeypatch):
        import builtins

        real_import = builtins.__import__

        def blocked(name, *a, **k):
            if name == "edge_tts":
                raise ImportError("No module named 'edge_tts'")
            return real_import(name, *a, **k)

        monkeypatch.setattr(builtins, "__import__", blocked)
        with pytest.raises(RuntimeError, match="not installed"):
            await EdgeTTSProvider().synthesize("hi", tmp_path / "ep.mp3")


class TestProviderShape:
    def test_class_attributes(self):
        p = EdgeTTSProvider()
        assert p.name == "edge_tts"
        assert p.sample_rate_hz == 24000
        assert p.default_format == "mp3"

    def test_satisfies_protocol(self):
        assert isinstance(EdgeTTSProvider(), TTSProvider)
