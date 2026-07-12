"""Unit tests for ChatterboxTTSProvider — mocked render_openai_tts (no sidecar)."""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest

from plugins.tts_provider import TTSProvider


@pytest.mark.unit
class TestChatterboxTTSProvider:
    def test_conforms_to_protocol(self):
        from services.tts_providers.chatterbox import ChatterboxTTSProvider
        assert isinstance(ChatterboxTTSProvider(), TTSProvider)
        assert ChatterboxTTSProvider().name == "chatterbox"

    async def test_synthesize_passes_emotion_knobs(self, tmp_path):
        from services.tts_providers.chatterbox import ChatterboxTTSProvider

        out = tmp_path / "cb.mp3"
        with patch(
            "services.tts_providers.chatterbox.render_openai_tts",
            new=AsyncMock(return_value=b"CBBYTES"),
        ) as m:
            result = await ChatterboxTTSProvider().synthesize(
                "Hello", out,
                config={
                    "base_url": "http://chatterbox:8000/v1",
                    "model": "chatterbox",
                    "exaggeration": "0.8",
                    "cfg_weight": "0.3",
                },
            )

        extra = m.await_args.kwargs["extra_body"]
        assert extra == {"exaggeration": 0.8, "cfg_weight": 0.3}
        assert out.read_bytes() == b"CBBYTES"
        assert result.metadata["engine"] == "chatterbox"
        assert result.metadata["exaggeration"] == 0.8

    async def test_bad_float_falls_back_to_default(self, tmp_path):
        from services.tts_providers.chatterbox import ChatterboxTTSProvider
        with patch(
            "services.tts_providers.chatterbox.render_openai_tts",
            new=AsyncMock(return_value=b"X"),
        ) as m:
            await ChatterboxTTSProvider().synthesize(
                "hi", tmp_path / "x.mp3",
                config={"exaggeration": "not-a-number"},
            )
        # non-numeric operator value falls back to 0.5, never crashes the render
        assert m.await_args.kwargs["extra_body"]["exaggeration"] == 0.5

    async def test_raises_when_sidecar_returns_none(self, tmp_path):
        from services.tts_providers.chatterbox import ChatterboxTTSProvider
        with patch(
            "services.tts_providers.chatterbox.render_openai_tts",
            new=AsyncMock(return_value=None),
        ):
            with pytest.raises(RuntimeError, match="chatterbox"):
                await ChatterboxTTSProvider().synthesize("hi", tmp_path / "x.mp3")

    async def test_synthesize_forwards_audio_prompt_path(self, tmp_path):
        """A configured voice-clone reference flows through to the sidecar
        request so the live pipeline can pin a production voice."""
        from services.tts_providers.chatterbox import ChatterboxTTSProvider

        with patch(
            "services.tts_providers.chatterbox.render_openai_tts",
            new=AsyncMock(return_value=b"CBBYTES"),
        ) as m:
            await ChatterboxTTSProvider().synthesize(
                "Hello", tmp_path / "cb.mp3",
                config={"audio_prompt_path": "/app/voices/podcast-voice.wav"},
            )
        extra = m.await_args.kwargs["extra_body"]
        assert extra["audio_prompt_path"] == "/app/voices/podcast-voice.wav"

    async def test_synthesize_omits_audio_prompt_path_when_unset(self, tmp_path):
        """Empty string (the app_settings unset sentinel) must NOT reach the
        sidecar as a literal '' — that would 400 (chatterbox_server.py checks
        os.path.exists on any truthy value). Omitting the key falls back to
        the sidecar's own built-in voice, matching today's zero-config
        bake-off behavior for OSS installs with no reference clip."""
        from services.tts_providers.chatterbox import ChatterboxTTSProvider

        with patch(
            "services.tts_providers.chatterbox.render_openai_tts",
            new=AsyncMock(return_value=b"X"),
        ) as m:
            await ChatterboxTTSProvider().synthesize(
                "hi", tmp_path / "x.mp3", config={"audio_prompt_path": ""},
            )
        assert "audio_prompt_path" not in m.await_args.kwargs["extra_body"]
