"""Unit tests for ``services/image_providers/ai_generation.py``.

The Ollama prompt-synthesis call and the downstream generator are both
mocked. Focus: config forwarding, fallback prompt on Ollama failure,
generator-not-found fallback to sdxl, metadata re-labelling.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from plugins.image_provider import ImageResult
from services.image_providers.ai_generation import AIGenerationProvider, _build_sdxl_prompt


def _mk_provider(name: str, results: list[ImageResult]):
    p = MagicMock()
    p.name = name
    p.fetch = AsyncMock(return_value=results)
    return p


@pytest.mark.unit
class TestAIGenerationProviderMetadata:
    def test_name(self):
        assert AIGenerationProvider.name == "ai_generation"

    def test_kind(self):
        assert AIGenerationProvider.kind == "generate"


@pytest.mark.unit
@pytest.mark.asyncio
class TestAIGenerationProviderFetch:
    async def test_empty_topic_returns_empty(self):
        result = await AIGenerationProvider().fetch("", {})
        assert result == []

    async def test_delegates_to_sdxl_by_default(self):
        sdxl = _mk_provider("sdxl", [
            ImageResult(url="file:///x.png", source="sdxl", metadata={}),
        ])

        with patch(
            "services.image_providers.ai_generation._build_sdxl_prompt",
            new=AsyncMock(return_value="a cinematic prompt"),
        ), \
             patch(
                "plugins.registry.get_image_providers",
                return_value=[sdxl],
             ):
            results = await AIGenerationProvider().fetch("Docker changed everything", {})

        assert len(results) == 1
        # Re-labelled with this provider's name.
        assert results[0].source == "ai_generation"
        # Downstream generator is tracked in metadata.
        assert results[0].metadata["downstream_generator"] == "sdxl"
        assert results[0].metadata["topic"] == "Docker changed everything"
        sdxl.fetch.assert_awaited_once_with("a cinematic prompt", {})

    async def test_config_forwarded_minus_prompt_model_and_generator(self):
        sdxl = _mk_provider("sdxl", [
            ImageResult(url="file:///x.png", source="sdxl"),
        ])

        with patch(
            "services.image_providers.ai_generation._build_sdxl_prompt",
            new=AsyncMock(return_value="p"),
        ), \
             patch(
                "plugins.registry.get_image_providers",
                return_value=[sdxl],
             ):
            await AIGenerationProvider().fetch("topic", {
                "prompt_model": "llama3:latest",
                "generator": "sdxl",
                "upload_to": "cloudinary",
                "negative_prompt": "no text",
            })

        passed = sdxl.fetch.await_args.args[1]
        # prompt_model + generator stripped; rest forwarded.
        assert "prompt_model" not in passed
        assert "generator" not in passed
        assert passed["upload_to"] == "cloudinary"
        assert passed["negative_prompt"] == "no text"

    async def test_unknown_generator_falls_back_to_sdxl(self):
        sdxl = _mk_provider("sdxl", [
            ImageResult(url="file:///x.png", source="sdxl"),
        ])

        with patch(
            "services.image_providers.ai_generation._build_sdxl_prompt",
            new=AsyncMock(return_value="p"),
        ), \
             patch(
                "plugins.registry.get_image_providers",
                return_value=[sdxl],  # "flux" not present
             ):
            results = await AIGenerationProvider().fetch("topic", {"generator": "flux"})

        assert len(results) == 1
        # The downstream tracking still says sdxl — the fallback happened.
        assert results[0].metadata["downstream_generator"] == "sdxl"

    async def test_no_sdxl_available_returns_empty(self):
        # Zero image providers at all — graceful.
        with patch(
            "services.image_providers.ai_generation._build_sdxl_prompt",
            new=AsyncMock(return_value="p"),
        ), \
             patch(
                "plugins.registry.get_image_providers",
                return_value=[],
             ):
            results = await AIGenerationProvider().fetch("topic", {})

        assert results == []


@pytest.mark.unit
@pytest.mark.asyncio
class TestBuildSDXLPrompt:
    async def test_ollama_success_returns_generated(self):
        resp = MagicMock()
        resp.raise_for_status = MagicMock()
        resp.json = MagicMock(return_value={"response": '"a detailed photoreal landscape scene"'})

        client = MagicMock()
        client.post = AsyncMock(return_value=resp)
        ctx = MagicMock()
        ctx.__aenter__ = AsyncMock(return_value=client)
        ctx.__aexit__ = AsyncMock(return_value=False)

        with patch("httpx.AsyncClient", return_value=ctx):
            result = await _build_sdxl_prompt("A post title", "llama3:latest")
        assert "photoreal" in result

    async def test_ollama_failure_returns_fallback(self):
        with patch("httpx.AsyncClient", side_effect=RuntimeError("boom")):
            result = await _build_sdxl_prompt("X", "llama3:latest")
        assert "photorealistic scene related to X" in result

    async def test_short_response_falls_back(self):
        resp = MagicMock()
        resp.raise_for_status = MagicMock()
        resp.json = MagicMock(return_value={"response": '"short"'})

        client = MagicMock()
        client.post = AsyncMock(return_value=resp)
        ctx = MagicMock()
        ctx.__aenter__ = AsyncMock(return_value=client)
        ctx.__aexit__ = AsyncMock(return_value=False)

        with patch("httpx.AsyncClient", return_value=ctx):
            result = await _build_sdxl_prompt("My blog post", "llama3:latest")
        assert "photorealistic scene" in result  # fell back
