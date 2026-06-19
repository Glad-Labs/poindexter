"""Unit tests for services.tts_service — Speaches TTS for podcast narration."""

from __future__ import annotations

import shutil
from unittest.mock import AsyncMock, MagicMock, patch

import pytest


class _Cfg:
    def __init__(
        self,
        enabled: bool = True,
        base_url: str = "http://speaches:8000/v1",
        *,
        fmt: str = "wav",
        remux: bool = False,
    ):
        self._enabled = enabled
        self._base_url = base_url
        self._fmt = fmt
        # Default False so legacy tests never spawn a real ffmpeg; the remux
        # tests opt in explicitly with remux=True.
        self._remux = remux

    def get(self, key, default=None):
        if key == "podcast_tts_enabled":
            return "true" if self._enabled else "false"
        if key == "podcast_tts_base_url":
            return self._base_url
        if key == "podcast_tts_voice":
            return "bf_emma"
        if key == "podcast_tts_model":
            return "speaches-ai/Kokoro-82M-v1.0-ONNX"
        if key == "podcast_tts_format":
            return self._fmt
        return default

    def get_bool(self, key, default=False):
        if key == "podcast_tts_enabled":
            return self._enabled
        if key == "podcast_tts_remux_enabled":
            return self._remux
        return default


@pytest.mark.unit
class TestTtsService:
    def test_is_enabled_true(self):
        from services.tts_service import is_tts_enabled
        assert is_tts_enabled(_Cfg(enabled=True)) is True

    def test_is_enabled_false(self):
        from services.tts_service import is_tts_enabled
        assert is_tts_enabled(_Cfg(enabled=False)) is False

    def test_is_enabled_none_site_config(self):
        from services.tts_service import is_tts_enabled
        assert is_tts_enabled(None) is False

    async def test_synthesize_returns_bytes_on_success(self, monkeypatch):
        """A 200 audio/wav response returns the audio bytes."""
        from services.tts_service import synthesize_speech

        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.headers = {"content-type": "audio/wav"}
        mock_response.content = b"RIFF_fake_audio_bytes"

        mock_client = AsyncMock()
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=None)
        mock_client.post = AsyncMock(return_value=mock_response)

        with patch("services.tts_service.httpx.AsyncClient", return_value=mock_client):
            result = await synthesize_speech("Hello world", site_config=_Cfg())

        assert result == b"RIFF_fake_audio_bytes"

    def test_default_format_is_mp3(self):
        """Speaches byte-concatenates per-segment WAVs for long input, so wav
        cuts off at ~24s; self-synchronizing MP3 frames play in full. The
        default MUST be mp3 (regression guard for #media-render-fixes)."""
        from services.tts_service import _DEFAULT_FORMAT
        assert _DEFAULT_FORMAT == "mp3"

    async def test_request_uses_default_mp3_when_unset(self):
        """When podcast_tts_format is unset, the request body carries the mp3
        default — not wav."""
        from services.tts_service import synthesize_speech

        class _NoFmtCfg(_Cfg):
            def get(self, key, default=None):
                if key == "podcast_tts_format":
                    return default  # exercise the _DEFAULT_FORMAT fallback
                return super().get(key, default)

        captured = {}
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.content = b"audio"

        async def _post(url, **kwargs):
            captured["json"] = kwargs.get("json")
            return mock_response

        mock_client = AsyncMock()
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=None)
        mock_client.post = AsyncMock(side_effect=_post)

        with patch("services.tts_service.httpx.AsyncClient", return_value=mock_client):
            await synthesize_speech("Hello", site_config=_NoFmtCfg())

        assert captured["json"]["response_format"] == "mp3"

    async def test_synthesize_returns_none_when_disabled(self):
        from services.tts_service import synthesize_speech
        result = await synthesize_speech("Hello", site_config=_Cfg(enabled=False))
        assert result is None

    async def test_synthesize_returns_none_on_error(self, monkeypatch):
        """A network error returns None, does not raise."""
        from services.tts_service import synthesize_speech

        mock_client = AsyncMock()
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=None)
        mock_client.post = AsyncMock(side_effect=ConnectionError("unreachable"))

        with patch("services.tts_service.httpx.AsyncClient", return_value=mock_client):
            result = await synthesize_speech("Hello", site_config=_Cfg())

        assert result is None

    async def test_synthesize_returns_none_on_non_200(self, monkeypatch):
        from services.tts_service import synthesize_speech

        mock_response = MagicMock()
        mock_response.status_code = 503
        mock_response.text = "Service Unavailable"

        mock_client = AsyncMock()
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=None)
        mock_client.post = AsyncMock(return_value=mock_response)

        with patch("services.tts_service.httpx.AsyncClient", return_value=mock_client):
            result = await synthesize_speech("Hello", site_config=_Cfg())

        assert result is None

    # ---- duration-header remux (Speaches byte-concatenation fix) ----

    async def test_remux_skips_non_self_syncing_format(self):
        """A concatenated WAV is unrecoverable under `-c copy` (only the first
        RIFF chunk survives), so the remux is a no-op and returns bytes as-is."""
        from services.tts_service import _remux_concatenated_audio
        raw = b"RIFF....fake-wav-bytes"
        assert await _remux_concatenated_audio(raw, "wav") == raw

    async def test_remux_fails_soft_when_ffmpeg_missing(self, monkeypatch):
        """No ffmpeg on PATH → return the raw bytes, never raise."""
        import services.tts_service as mod
        monkeypatch.setattr(mod.shutil, "which", lambda _name: None)
        raw = b"ID3fake-mp3-bytes"
        assert await mod._remux_concatenated_audio(raw, "mp3") == raw

    async def test_remux_fails_soft_on_ffmpeg_error(self, monkeypatch):
        """ffmpeg exits non-zero → return the raw bytes, never raise."""
        import services.tts_service as mod
        monkeypatch.setattr(mod.shutil, "which", lambda _name: "/usr/bin/ffmpeg")

        class _Proc:
            returncode = 1

            async def communicate(self):
                return (b"", b"moov atom not found")

        async def _fake_exec(*_a, **_k):
            return _Proc()

        monkeypatch.setattr(mod.asyncio, "create_subprocess_exec", _fake_exec)
        raw = b"ID3fake-mp3-bytes"
        assert await mod._remux_concatenated_audio(raw, "mp3") == raw

    async def test_remux_returns_ffmpeg_output_on_success(self, monkeypatch):
        """On success the remuxed file's bytes are returned (not the input)."""
        import services.tts_service as mod
        monkeypatch.setattr(mod.shutil, "which", lambda _name: "/usr/bin/ffmpeg")

        async def _fake_exec(*args, **_k):
            mod._write_bytes(args[-1], b"FIXED-mp3-with-correct-header")  # dst

            class _Proc:
                returncode = 0

                async def communicate(self):
                    return (b"", b"")

            return _Proc()

        monkeypatch.setattr(mod.asyncio, "create_subprocess_exec", _fake_exec)
        out = await mod._remux_concatenated_audio(b"raw-concat-bytes", "mp3")
        assert out == b"FIXED-mp3-with-correct-header"

    async def test_synthesize_applies_remux_for_mp3(self, monkeypatch):
        """synthesize_speech runs the remux on the Speaches bytes for mp3 and
        returns the remuxed result — wires the single TTS boundary."""
        import services.tts_service as mod
        from services.tts_service import synthesize_speech

        called = {}

        async def _fake_remux(audio_bytes, fmt):
            called["args"] = (audio_bytes, fmt)
            return b"REMUXED"

        monkeypatch.setattr(mod, "_remux_concatenated_audio", _fake_remux)

        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.content = b"RAW-from-speaches"
        mock_client = AsyncMock()
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=None)
        mock_client.post = AsyncMock(return_value=mock_response)

        with patch("services.tts_service.httpx.AsyncClient", return_value=mock_client):
            result = await synthesize_speech(
                "Hi", site_config=_Cfg(fmt="mp3", remux=True)
            )

        assert called["args"] == (b"RAW-from-speaches", "mp3")
        assert result == b"REMUXED"

    async def test_synthesize_skips_remux_when_disabled(self, monkeypatch):
        """podcast_tts_remux_enabled=false → remux never runs, raw bytes flow."""
        import services.tts_service as mod
        from services.tts_service import synthesize_speech

        async def _boom(*_a, **_k):
            raise AssertionError("remux must not run when disabled")

        monkeypatch.setattr(mod, "_remux_concatenated_audio", _boom)

        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.content = b"RAW"
        mock_client = AsyncMock()
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=None)
        mock_client.post = AsyncMock(return_value=mock_response)

        with patch("services.tts_service.httpx.AsyncClient", return_value=mock_client):
            result = await synthesize_speech(
                "Hi", site_config=_Cfg(fmt="mp3", remux=False)
            )

        assert result == b"RAW"

    @pytest.mark.skipif(
        not (shutil.which("ffmpeg") and shutil.which("ffprobe")),
        reason="needs ffmpeg + ffprobe on PATH",
    )
    async def test_remux_preserves_duration_of_wellformed_mp3(self, tmp_path):
        """A `-c copy` remux of a valid mp3 must preserve its duration
        (lossless, no truncation) — guards the remux against dropping audio."""
        import subprocess

        from services.tts_service import _remux_concatenated_audio

        src = tmp_path / "tone.mp3"
        subprocess.run(
            ["ffmpeg", "-hide_banner", "-loglevel", "error", "-y", "-f", "lavfi",
             "-i", "sine=frequency=440:duration=3", "-c:a", "libmp3lame", str(src)],
            check=True,
        )
        out = await _remux_concatenated_audio(src.read_bytes(), "mp3")
        dst = tmp_path / "out.mp3"
        dst.write_bytes(out)

        def _dur(p) -> float:
            r = subprocess.run(
                ["ffprobe", "-v", "error", "-show_entries", "format=duration",
                 "-of", "default=nokey=1:noprint_wrappers=1", str(p)],
                capture_output=True, text=True, check=True,
            )
            return float(r.stdout.strip())

        assert abs(_dur(dst) - _dur(src)) < 0.5
