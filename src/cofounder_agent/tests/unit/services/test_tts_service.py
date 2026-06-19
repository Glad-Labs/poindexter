"""Unit tests for services.tts_service — Speaches TTS for podcast narration."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest


class _Cfg:
    def __init__(self, enabled: bool = True, base_url: str = "http://speaches:8000/v1"):
        self._enabled = enabled
        self._base_url = base_url

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
            return "wav"
        return default

    def get_bool(self, key, default=False):
        if key == "podcast_tts_enabled":
            return self._enabled
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
