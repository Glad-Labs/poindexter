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
        loudnorm: bool = False,
    ):
        self._enabled = enabled
        self._base_url = base_url
        self._fmt = fmt
        # Default False so legacy tests never spawn a real ffmpeg; the remux
        # tests opt in explicitly with remux=True.
        self._remux = remux
        # Default False so legacy tests never invoke the loudnorm render path;
        # the EBU R128 tests opt in explicitly with loudnorm=True. Production
        # default is true (settings_defaults.podcast_tts_loudnorm_enabled).
        self._loudnorm = loudnorm

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
        if key == "podcast_tts_loudnorm_enabled":
            return self._loudnorm
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

        async def _fake_remux(
            audio_bytes, fmt, *, mode="reencode", bitrate="96k", **_extra
        ):
            called["args"] = (audio_bytes, fmt)
            called["mode"] = mode
            called["bitrate"] = bitrate
            called["extra"] = _extra
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
        assert called["mode"] == "reencode"  # default mode resolved from config
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

    # ---- re-encode mode (collapse Speaches' byte-concatenated segments) ----

    async def test_remux_reencode_uses_lame_for_mp3(self, monkeypatch):
        """``mode='reencode'`` builds an ffmpeg re-encode (libmp3lame + bitrate),
        NOT ``-c copy`` — that is what collapses the byte-concatenated, multi-
        header Speaches stream into one clean single-stream MP3."""
        import services.tts_service as mod
        monkeypatch.setattr(mod.shutil, "which", lambda _name: "/usr/bin/ffmpeg")
        captured = {}

        async def _fake_exec(*args, **_k):
            captured["argv"] = list(args)
            mod._write_bytes(args[-1], b"REENCODED")

            class _Proc:
                returncode = 0

                async def communicate(self):
                    return (b"", b"")

            return _Proc()

        monkeypatch.setattr(mod.asyncio, "create_subprocess_exec", _fake_exec)
        out = await mod._remux_concatenated_audio(
            b"raw", "mp3", mode="reencode", bitrate="96k"
        )
        assert out == b"REENCODED"
        argv = captured["argv"]
        assert "libmp3lame" in argv
        assert "96k" in argv
        assert "copy" not in argv

    async def test_remux_copy_mode_preserves_c_copy(self, monkeypatch):
        """``mode='copy'`` keeps the legacy lossless ``-c copy`` path for back-
        compat (operators who want zero re-encode can set it)."""
        import services.tts_service as mod
        monkeypatch.setattr(mod.shutil, "which", lambda _name: "/usr/bin/ffmpeg")
        captured = {}

        async def _fake_exec(*args, **_k):
            captured["argv"] = list(args)
            mod._write_bytes(args[-1], b"COPIED")

            class _Proc:
                returncode = 0

                async def communicate(self):
                    return (b"", b"")

            return _Proc()

        monkeypatch.setattr(mod.asyncio, "create_subprocess_exec", _fake_exec)
        out = await mod._remux_concatenated_audio(b"raw", "mp3", mode="copy")
        assert out == b"COPIED"
        assert "copy" in captured["argv"]
        assert "libmp3lame" not in captured["argv"]

    def test_remux_mode_default_is_reencode(self):
        """The module default mode is reencode — the structural fix is the
        default, not opt-in (copy stays available for back-compat)."""
        from services.tts_service import _DEFAULT_REMUX_MODE
        assert _DEFAULT_REMUX_MODE == "reencode"

    @pytest.mark.skipif(
        not (shutil.which("ffmpeg") and shutil.which("ffprobe")),
        reason="needs ffmpeg + ffprobe on PATH",
    )
    async def test_reencode_collapses_concatenated_segments_to_single_stream(
        self, tmp_path
    ):
        """The real fix: a byte-concatenated 2-segment MP3 (≥2 embedded Xing/Info
        headers, exactly the shape Speaches emits) re-encodes to ONE clean stream
        (≤1 header) with BOTH segments preserved (not truncated to the first)."""
        import subprocess

        from services.tts_service import _remux_concatenated_audio

        def _mk(path, freq, dur):
            subprocess.run(
                ["ffmpeg", "-hide_banner", "-loglevel", "error", "-y", "-f", "lavfi",
                 "-i", f"sine=frequency={freq}:duration={dur}",
                 "-c:a", "libmp3lame", str(path)],
                check=True,
            )

        a = tmp_path / "a.mp3"
        b = tmp_path / "b.mp3"
        _mk(a, 440, 2)
        _mk(b, 660, 2)
        # Byte-concatenate, exactly how Speaches glues its internal segments.
        concatenated = a.read_bytes() + b.read_bytes()
        headers_in = concatenated.count(b"Xing") + concatenated.count(b"Info")
        assert headers_in >= 2  # each LAME segment carries its own header

        out = await _remux_concatenated_audio(
            concatenated, "mp3", mode="reencode", bitrate="96k"
        )
        headers_out = out.count(b"Xing") + out.count(b"Info")
        assert headers_out <= 1  # collapsed to a single clean stream

        dst = tmp_path / "out.mp3"
        dst.write_bytes(out)
        r = subprocess.run(
            ["ffprobe", "-v", "error", "-show_entries", "format=duration",
             "-of", "default=nokey=1:noprint_wrappers=1", str(dst)],
            capture_output=True, text=True, check=True,
        )
        # ~4s total (both 2s segments), NOT truncated to the first segment's 2s.
        assert float(r.stdout.strip()) > 3.5

    # ---- EBU R128 loudness normalization (audio_clipping fix) ----

    def test_loudnorm_defaults(self):
        """Loudness target defaults to the podcast standard (-16 LUFS) with
        true-peak headroom (-1.5 dBTP) — so Kokoro's full-scale output is pulled
        below the qa.audio -0.1 dBFS clip gate instead of pinning at 0.0 dBFS."""
        from services.tts_service import (
            _DEFAULT_LOUDNORM_I,
            _DEFAULT_LOUDNORM_TP,
        )
        assert _DEFAULT_LOUDNORM_I == "-16"
        assert _DEFAULT_LOUDNORM_TP == "-1.5"

    async def test_remux_applies_loudnorm_filter(self, monkeypatch):
        """loudnorm=True injects `-af loudnorm=I=..:TP=..:LRA=..` so the rendered
        narration is normalized to the loudness target with true-peak headroom
        (the root-cause fix for the 0.0 dBFS audio_clipping finding)."""
        import services.tts_service as mod
        monkeypatch.setattr(mod.shutil, "which", lambda _name: "/usr/bin/ffmpeg")
        captured = {}

        async def _fake_exec(*args, **_k):
            captured["argv"] = list(args)
            mod._write_bytes(args[-1], b"LOUDNORMED")

            class _Proc:
                returncode = 0

                async def communicate(self):
                    return (b"", b"")

            return _Proc()

        monkeypatch.setattr(mod.asyncio, "create_subprocess_exec", _fake_exec)
        out = await mod._remux_concatenated_audio(
            b"raw", "mp3", loudnorm=True,
            loudnorm_i="-16", loudnorm_tp="-1.5", loudnorm_lra="11",
            loudnorm_ar="44100",
        )
        assert out == b"LOUDNORMED"
        argv = captured["argv"]
        assert "-af" in argv
        af = argv[argv.index("-af") + 1]
        assert "loudnorm=I=-16:TP=-1.5:LRA=11" in af
        # loudnorm internally upsamples to 192 kHz — resample back to a sane rate.
        assert "aresample=44100" in af

    async def test_remux_loudnorm_forces_reencode_over_copy(self, monkeypatch):
        """A filter graph cannot ride on `-c copy`, so loudnorm=True forces a
        re-encode even when mode='copy' is requested."""
        import services.tts_service as mod
        monkeypatch.setattr(mod.shutil, "which", lambda _name: "/usr/bin/ffmpeg")
        captured = {}

        async def _fake_exec(*args, **_k):
            captured["argv"] = list(args)
            mod._write_bytes(args[-1], b"OUT")

            class _Proc:
                returncode = 0

                async def communicate(self):
                    return (b"", b"")

            return _Proc()

        monkeypatch.setattr(mod.asyncio, "create_subprocess_exec", _fake_exec)
        await mod._remux_concatenated_audio(
            b"raw", "mp3", mode="copy", loudnorm=True
        )
        argv = captured["argv"]
        assert "copy" not in argv       # a filter graph can't carry -c copy
        assert "libmp3lame" in argv     # forced re-encode
        assert "-af" in argv

    async def test_remux_without_loudnorm_has_no_af(self, monkeypatch):
        """Default (loudnorm=False) keeps the plain header-repair re-encode with
        NO audio filter — guards against always-on processing."""
        import services.tts_service as mod
        monkeypatch.setattr(mod.shutil, "which", lambda _name: "/usr/bin/ffmpeg")
        captured = {}

        async def _fake_exec(*args, **_k):
            captured["argv"] = list(args)
            mod._write_bytes(args[-1], b"OUT")

            class _Proc:
                returncode = 0

                async def communicate(self):
                    return (b"", b"")

            return _Proc()

        monkeypatch.setattr(mod.asyncio, "create_subprocess_exec", _fake_exec)
        await mod._remux_concatenated_audio(b"raw", "mp3", mode="reencode")
        assert "-af" not in captured["argv"]

    async def test_synthesize_applies_loudnorm_for_mp3(self, monkeypatch):
        """synthesize_speech resolves the loudnorm config and threads it to the
        render boundary so podcast + both video lanes inherit the headroom."""
        import services.tts_service as mod
        from services.tts_service import synthesize_speech

        called: dict = {}

        async def _fake_remux(audio_bytes, fmt, **kwargs):
            called.update(kwargs)
            called["fmt"] = fmt
            return b"OUT"

        monkeypatch.setattr(mod, "_remux_concatenated_audio", _fake_remux)

        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.content = b"RAW"
        mock_client = AsyncMock()
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=None)
        mock_client.post = AsyncMock(return_value=mock_response)

        with patch("services.tts_service.httpx.AsyncClient", return_value=mock_client):
            await synthesize_speech(
                "Hi", site_config=_Cfg(fmt="mp3", remux=True, loudnorm=True)
            )

        assert called["loudnorm"] is True
        assert called["loudnorm_i"] == "-16"
        assert called["loudnorm_tp"] == "-1.5"
        assert called["loudnorm_lra"] == "11"

    async def test_synthesize_loudnorm_runs_even_when_remux_disabled(
        self, monkeypatch
    ):
        """Loudness normalization is its own concern: it must run even when the
        header-repair remux is disabled — otherwise disabling remux silently
        re-introduces the clipping the fix removes."""
        import services.tts_service as mod
        from services.tts_service import synthesize_speech

        called: dict = {}

        async def _fake_remux(audio_bytes, fmt, **kwargs):
            called["ran"] = True
            called.update(kwargs)
            return b"OUT"

        monkeypatch.setattr(mod, "_remux_concatenated_audio", _fake_remux)

        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.content = b"RAW"
        mock_client = AsyncMock()
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=None)
        mock_client.post = AsyncMock(return_value=mock_response)

        with patch("services.tts_service.httpx.AsyncClient", return_value=mock_client):
            await synthesize_speech(
                "Hi", site_config=_Cfg(fmt="mp3", remux=False, loudnorm=True)
            )

        assert called.get("ran") is True
        assert called.get("loudnorm") is True

    @pytest.mark.skipif(
        not (shutil.which("ffmpeg") and shutil.which("ffprobe")),
        reason="needs ffmpeg + ffprobe on PATH",
    )
    async def test_loudnorm_brings_full_scale_audio_below_clip_gate(self, tmp_path):
        """End-to-end proof against the reported finding: a full-scale (0.0 dBFS)
        source — exactly what Kokoro hands the probe — run through the loudnorm
        render lands BELOW the qa.audio -0.1 dBFS clip gate. This is the literal
        condition the audio_clipping finding flags."""
        import re
        import subprocess

        from modules.content.atoms.qa_audio import _DEFAULT_MAX_VOLUME_CLIP_DB
        from services.tts_service import _remux_concatenated_audio

        def _max_volume_db(path) -> float:
            r = subprocess.run(
                ["ffmpeg", "-hide_banner", "-nostats", "-i", str(path),
                 "-af", "volumedetect", "-f", "null", "-"],
                capture_output=True, text=True,
            )
            m = re.search(r"max_volume:\s*([-\d.]+)\s*dB", r.stderr)
            assert m, f"no max_volume in ffmpeg output: {r.stderr[-300:]}"
            return float(m.group(1))

        # Overdrive a sine so samples clamp to ±full-scale — a guaranteed
        # 0.0 dBFS source, the hot-as-Kokoro input the finding reports.
        src = tmp_path / "hot.mp3"
        subprocess.run(
            ["ffmpeg", "-hide_banner", "-loglevel", "error", "-y", "-f", "lavfi",
             "-i", "sine=frequency=300:duration=2", "-af", "volume=30dB",
             "-c:a", "libmp3lame", str(src)],
            check=True,
        )
        # Precondition: the source really is at/above the clip gate.
        assert _max_volume_db(src) >= _DEFAULT_MAX_VOLUME_CLIP_DB

        out = await _remux_concatenated_audio(
            src.read_bytes(), "mp3", loudnorm=True,
            loudnorm_i="-16", loudnorm_tp="-1.5", loudnorm_lra="11",
            loudnorm_ar="44100",
        )
        dst = tmp_path / "norm.mp3"
        dst.write_bytes(out)

        # The fix: peak is now comfortably under the qa.audio clip threshold.
        assert _max_volume_db(dst) < _DEFAULT_MAX_VOLUME_CLIP_DB
