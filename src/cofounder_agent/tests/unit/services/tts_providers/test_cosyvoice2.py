"""Unit tests for CosyVoice2TTSProvider — mocked render_openai_tts (no sidecar)."""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest

from plugins.tts_provider import TTSProvider


@pytest.mark.unit
class TestCosyVoice2TTSProvider:
    def test_conforms_to_protocol(self):
        from services.tts_providers.cosyvoice2 import CosyVoice2TTSProvider
        assert isinstance(CosyVoice2TTSProvider(), TTSProvider)
        assert CosyVoice2TTSProvider().name == "cosyvoice2"

    async def test_synthesize_passes_instruct_and_writes_file(self, tmp_path):
        from services.tts_providers.cosyvoice2 import CosyVoice2TTSProvider

        out = tmp_path / "cosy.mp3"
        with patch(
            "services.tts_providers.cosyvoice2.render_openai_tts",
            new=AsyncMock(return_value=b"MP3BYTES"),
        ) as m:
            result = await CosyVoice2TTSProvider().synthesize(
                "Hello world", out,
                voice=None,
                config={
                    "base_url": "http://cosyvoice2:8000/v1",
                    "model": "cosyvoice2",
                    "instruct": "speak cheerfully",
                },
            )

        # emotion instruction is threaded through extra_body
        kwargs = m.await_args.kwargs
        assert kwargs["extra_body"] == {"instruct": "speak cheerfully"}
        assert kwargs["base_url"] == "http://cosyvoice2:8000/v1"
        assert kwargs["text"] == "Hello world"
        # file written + result populated
        assert out.read_bytes() == b"MP3BYTES"
        assert result.audio_path == out
        assert result.file_size_bytes == len(b"MP3BYTES")
        assert result.metadata["engine"] == "cosyvoice2"

    async def test_synthesize_raises_when_sidecar_returns_none(self, tmp_path):
        from services.tts_providers.cosyvoice2 import CosyVoice2TTSProvider

        with patch(
            "services.tts_providers.cosyvoice2.render_openai_tts",
            new=AsyncMock(return_value=None),
        ):
            with pytest.raises(RuntimeError, match="cosyvoice2"):
                await CosyVoice2TTSProvider().synthesize(
                    "hi", tmp_path / "x.mp3", config={"base_url": "http://c:8000/v1"},
                )

    async def test_synthesize_rejects_empty_text(self, tmp_path):
        from services.tts_providers.cosyvoice2 import CosyVoice2TTSProvider
        with pytest.raises(ValueError):
            await CosyVoice2TTSProvider().synthesize("   ", tmp_path / "x.mp3")
