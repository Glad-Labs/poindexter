"""Unit tests for services/caption_providers/speaches.py.

The SpeachesCaptionProvider reuses the already-running Speaches
(faster-whisper) sidecar for caption transcription instead of a second,
separate whisper.cpp install — POSTing narration audio to Speaches'
OpenAI-compatible ``/audio/transcriptions`` endpoint and turning the
returned ``verbose_json`` segments into an SRT track.

External I/O (``httpx.AsyncClient``) is mocked.
"""

from __future__ import annotations

from typing import Any
from unittest.mock import patch

import pytest

from plugins.caption_provider import CaptionProvider, CaptionResult


class _StubSiteConfig:
    """site_config double — prefixes the provider namespace automatically."""

    def __init__(self, mapping: dict[str, Any] | None = None) -> None:
        self._mapping = {
            f"plugin.caption_provider.speaches.{k}": v
            for k, v in (mapping or {}).items()
        }

    def get(self, key: str, default: Any = None) -> Any:
        return self._mapping.get(key, default)


def _make_provider(mapping: dict[str, Any] | None = None):
    from services.caption_providers.speaches import SpeachesCaptionProvider

    return SpeachesCaptionProvider(site_config=_StubSiteConfig(mapping or {}))


class _FakeResp:
    def __init__(self, status_code: int = 200, payload: dict | None = None, text: str = "") -> None:
        self.status_code = status_code
        self._payload = payload or {}
        self.text = text

    def json(self) -> dict:
        return self._payload


class _FakeAsyncClient:
    """Stand-in for httpx.AsyncClient — async context manager + post()."""

    def __init__(self, resp: _FakeResp) -> None:
        self._resp = resp
        self.posted: dict[str, Any] = {}

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False

    async def post(self, url, **kwargs):
        self.posted = {"url": url, **kwargs}
        return self._resp


_VERBOSE_JSON = {
    "task": "transcribe",
    "language": "en",
    "duration": 5.0,
    "text": "Hello world. Second line.",
    "segments": [
        {"id": 0, "start": 0.0, "end": 2.5, "text": " Hello world."},
        {"id": 1, "start": 2.5, "end": 5.0, "text": " Second line."},
    ],
}


# ---------------------------------------------------------------------------
# Protocol conformance
# ---------------------------------------------------------------------------


class TestProtocolConformance:
    def test_satisfies_caption_provider_protocol(self):
        from services.caption_providers.speaches import SpeachesCaptionProvider

        assert isinstance(SpeachesCaptionProvider(), CaptionProvider)

    def test_class_attributes(self):
        from services.caption_providers.speaches import SpeachesCaptionProvider

        p = SpeachesCaptionProvider()
        assert p.name == "speaches"
        assert p.supported_languages == ()
        assert p.supports_diarization is False


# ---------------------------------------------------------------------------
# Bail-loudly gates
# ---------------------------------------------------------------------------


class TestDisabledGate:
    @pytest.mark.asyncio
    async def test_disabled_returns_failure(self, tmp_path):
        audio = tmp_path / "in.mp3"
        audio.write_bytes(b"\x00" * 16)
        provider = _make_provider({"enabled": False})
        result = await provider.transcribe(audio_path=str(audio))
        assert isinstance(result, CaptionResult)
        assert result.success is False
        assert "disabled" in (result.error or "").lower()

    @pytest.mark.asyncio
    async def test_disabled_does_not_call_http(self, tmp_path):
        audio = tmp_path / "in.mp3"
        audio.write_bytes(b"\x00" * 16)
        provider = _make_provider({"enabled": False})
        with patch("services.caption_providers.speaches.httpx.AsyncClient") as mock_client:
            await provider.transcribe(audio_path=str(audio))
        mock_client.assert_not_called()


class TestMissingAudio:
    @pytest.mark.asyncio
    async def test_missing_audio_path_fails(self, tmp_path):
        provider = _make_provider({})
        result = await provider.transcribe(audio_path=str(tmp_path / "nope.mp3"))
        assert result.success is False
        assert "does not exist" in (result.error or "").lower()

    @pytest.mark.asyncio
    async def test_empty_audio_path_fails(self):
        provider = _make_provider({})
        result = await provider.transcribe(audio_path="")
        assert result.success is False


# ---------------------------------------------------------------------------
# Happy path — verbose_json → segments + SRT
# ---------------------------------------------------------------------------


class TestTranscribeHappyPath:
    @pytest.mark.asyncio
    async def test_success_builds_segments_and_srt(self, tmp_path):
        audio = tmp_path / "narration.mp3"
        audio.write_bytes(b"\x00" * 32)
        provider = _make_provider({"model": "Systran/faster-whisper-medium"})

        fake = _FakeAsyncClient(_FakeResp(200, _VERBOSE_JSON))
        with patch(
            "services.caption_providers.speaches.httpx.AsyncClient",
            return_value=fake,
        ):
            result = await provider.transcribe(audio_path=str(audio))

        assert result.success is True
        assert result.error is None
        assert result.language == "en"
        assert len(result.segments) == 2
        assert result.segments[0].text == "Hello world."
        assert result.segments[0].start_s == 0.0
        assert result.segments[1].end_s == 5.0
        # SRT carries both cues with HH:MM:SS,mmm timing.
        assert "00:00:00,000 --> 00:00:02,500" in result.srt_text
        assert "00:00:02,500 --> 00:00:05,000" in result.srt_text
        assert "Hello world." in result.srt_text
        assert "Second line." in result.srt_text

    @pytest.mark.asyncio
    async def test_posts_verbose_json_to_transcriptions_endpoint(self, tmp_path):
        audio = tmp_path / "narration.mp3"
        audio.write_bytes(b"\x00" * 32)
        provider = _make_provider({"base_url": "http://speaches:8000/v1"})

        fake = _FakeAsyncClient(_FakeResp(200, _VERBOSE_JSON))
        with patch(
            "services.caption_providers.speaches.httpx.AsyncClient",
            return_value=fake,
        ):
            await provider.transcribe(audio_path=str(audio), language_hint="en")

        assert fake.posted["url"] == "http://speaches:8000/v1/audio/transcriptions"
        assert fake.posted["data"]["response_format"] == "verbose_json"
        assert fake.posted["data"]["language"] == "en"
        assert "file" in fake.posted["files"]

    @pytest.mark.asyncio
    async def test_empty_segments_is_failure(self, tmp_path):
        audio = tmp_path / "narration.mp3"
        audio.write_bytes(b"\x00" * 32)
        provider = _make_provider({})
        fake = _FakeAsyncClient(_FakeResp(200, {"language": "en", "segments": []}))
        with patch(
            "services.caption_providers.speaches.httpx.AsyncClient",
            return_value=fake,
        ):
            result = await provider.transcribe(audio_path=str(audio))
        assert result.success is False
        assert not result.srt_text


# ---------------------------------------------------------------------------
# initial_prompt — vocabulary bias for proper nouns (brand-name ASR fix)
#
# faster-whisper mishears "Glad Labs" as "GLAAD Labs". The OpenAI-compatible
# transcriptions endpoint accepts a ``prompt`` field that Speaches forwards as
# faster-whisper's ``initial_prompt``, biasing the decoder toward the supplied
# vocabulary. The provider reads it from
# ``plugin.caption_provider.speaches.initial_prompt`` and forwards it on the
# multipart ``data`` — but only when it carries actual text, so an unset/blank
# setting transcribes exactly as before.
# ---------------------------------------------------------------------------


_PROMPT_VOCAB = "Poindexter, Kokoro, ONNX"


class TestInitialPromptBias:
    @pytest.mark.asyncio
    async def test_configured_prompt_is_forwarded_in_data(self, tmp_path):
        audio = tmp_path / "narration.mp3"
        audio.write_bytes(b"\x00" * 32)
        provider = _make_provider({"initial_prompt": _PROMPT_VOCAB})

        fake = _FakeAsyncClient(_FakeResp(200, _VERBOSE_JSON))
        with patch(
            "services.caption_providers.speaches.httpx.AsyncClient",
            return_value=fake,
        ):
            await provider.transcribe(audio_path=str(audio))

        assert fake.posted["data"]["prompt"] == _PROMPT_VOCAB

    @pytest.mark.asyncio
    async def test_unset_prompt_is_omitted_from_data(self, tmp_path):
        audio = tmp_path / "narration.mp3"
        audio.write_bytes(b"\x00" * 32)
        provider = _make_provider({})  # no initial_prompt configured

        fake = _FakeAsyncClient(_FakeResp(200, _VERBOSE_JSON))
        with patch(
            "services.caption_providers.speaches.httpx.AsyncClient",
            return_value=fake,
        ):
            await provider.transcribe(audio_path=str(audio))

        assert "prompt" not in fake.posted["data"]

    @pytest.mark.asyncio
    async def test_blank_prompt_is_omitted_from_data(self, tmp_path):
        # '' is the NOT-NULL "unset" sentinel; whitespace-only is treated the
        # same so a stray space in app_settings doesn't ship a useless prompt.
        audio = tmp_path / "narration.mp3"
        audio.write_bytes(b"\x00" * 32)
        provider = _make_provider({"initial_prompt": "   "})

        fake = _FakeAsyncClient(_FakeResp(200, _VERBOSE_JSON))
        with patch(
            "services.caption_providers.speaches.httpx.AsyncClient",
            return_value=fake,
        ):
            await provider.transcribe(audio_path=str(audio))

        assert "prompt" not in fake.posted["data"]


# ---------------------------------------------------------------------------
# Error handling — never raises
# ---------------------------------------------------------------------------


class TestErrorHandling:
    @pytest.mark.asyncio
    async def test_http_error_returns_failure(self, tmp_path):
        audio = tmp_path / "narration.mp3"
        audio.write_bytes(b"\x00" * 32)
        provider = _make_provider({})
        fake = _FakeAsyncClient(_FakeResp(500, {}, text="internal error"))
        with patch(
            "services.caption_providers.speaches.httpx.AsyncClient",
            return_value=fake,
        ):
            result = await provider.transcribe(audio_path=str(audio))
        assert result.success is False
        assert "500" in (result.error or "")

    @pytest.mark.asyncio
    async def test_transport_exception_does_not_raise(self, tmp_path):
        audio = tmp_path / "narration.mp3"
        audio.write_bytes(b"\x00" * 32)
        provider = _make_provider({})

        class _Boom:
            def __init__(self, *a, **k):
                pass

            async def __aenter__(self):
                raise RuntimeError("connection refused")

            async def __aexit__(self, *exc):
                return False

        with patch("services.caption_providers.speaches.httpx.AsyncClient", _Boom):
            result = await provider.transcribe(audio_path=str(audio))
        assert result.success is False
        assert "connection refused" in (result.error or "")


# ---------------------------------------------------------------------------
# SRT formatting helpers
# ---------------------------------------------------------------------------


class TestSrtHelpers:
    def test_format_ts_zero(self):
        from services.caption_providers.speaches import _format_ts

        assert _format_ts(0.0) == "00:00:00,000"

    def test_format_ts_hours_minutes_millis(self):
        from services.caption_providers.speaches import _format_ts

        assert _format_ts(3661.5) == "01:01:01,500"

    def test_segments_to_srt_numbered_blocks(self):
        from plugins.caption_provider import CaptionSegment
        from services.caption_providers.speaches import _segments_to_srt

        srt = _segments_to_srt([
            CaptionSegment(start_s=0.0, end_s=1.0, text="one"),
            CaptionSegment(start_s=1.0, end_s=2.0, text="two"),
        ])
        assert srt.startswith("1\n")
        assert "2\n" in srt
        assert "00:00:00,000 --> 00:00:01,000" in srt

    def test_segments_to_srt_empty_is_empty(self):
        from services.caption_providers.speaches import _segments_to_srt

        assert _segments_to_srt([]) == ""
