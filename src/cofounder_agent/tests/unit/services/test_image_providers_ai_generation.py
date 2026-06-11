"""Unit tests for ``services/image_providers/ai_generation.py``.

The Ollama prompt-synthesis call and the downstream generator are both
mocked. Focus: config forwarding, fallback prompt on Ollama failure,
generator-not-found fallback to sdxl, metadata re-labelling.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from plugins.image_provider import ImageResult
from services.image_providers.ai_generation import (
    AIGenerationProvider,
    _build_sdxl_prompt,
    _scrub_human_terms,
)


@pytest.mark.unit
class TestScrubHumanTerms:
    """#522 — anthropomorphic terms must be stripped from the positive prompt."""

    def test_strips_people_and_hands(self):
        cleaned, had = _scrub_human_terms(
            "a developer typing with both hands at a desk, people in background"
        )
        assert had is True
        for term in ("developer", "hands", "people"):
            assert term not in cleaned.lower()

    def test_clean_prompt_unchanged(self):
        prompt = "an isometric server rack, neon lighting, 4k, detailed"
        cleaned, had = _scrub_human_terms(prompt)
        assert had is False
        assert cleaned == prompt

    @pytest.mark.asyncio
    async def test_fallback_has_no_human_or_negation_tokens(self):
        # Ollama unreachable -> fallback path; must be human-free and must NOT
        # carry a counterproductive "no people" token in the POSITIVE prompt.
        with patch("services.image_providers.ai_generation.http_client", None), \
             patch("httpx.AsyncClient", side_effect=RuntimeError("offline")):
            out = await _build_sdxl_prompt("GPU benchmarks", "qwen", site_config=None)
        low = out.lower()
        assert "no people" not in low
        for term in ("people", "person", "hands", "face"):
            assert term not in low


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

    async def test_dispatches_when_pool_available(self):
        # poindexter#535: when site_config exposes a pool, the call routes
        # through dispatch_complete (provider-swappability + cost tracking)
        # rather than a direct httpx POST to /api/generate.
        completion = MagicMock()
        completion.text = '"an isometric data-center scene, cinematic lighting, 4k"'

        site_config = MagicMock()
        site_config._pool = MagicMock()  # truthy pool → dispatcher path

        dispatch = AsyncMock(return_value=completion)
        # No httpx client is wired; if the code took the fallback path it would
        # try httpx.AsyncClient and trip the AssertionError below.
        with patch(
            "services.llm_providers.dispatcher.dispatch_complete", new=dispatch
        ), patch("httpx.AsyncClient", side_effect=AssertionError("must not hit httpx")):
            result = await _build_sdxl_prompt(
                "Data centers", "llama3:latest", site_config=site_config,
            )

        dispatch.assert_awaited_once()
        await_args = dispatch.await_args
        assert await_args is not None
        kwargs = await_args.kwargs
        assert kwargs["pool"] is site_config._pool
        assert kwargs["model"] == "llama3:latest"
        assert kwargs["messages"][0]["role"] == "user"
        # Surrounding quotes stripped; scrub leaves the object/scene prompt.
        assert "isometric data-center scene" in result

    async def test_dispatch_failure_falls_back(self):
        # A dispatcher exception must degrade to the generic fallback prompt,
        # never propagate out of the image-provider path.
        site_config = MagicMock()
        site_config._pool = MagicMock()

        with patch(
            "services.llm_providers.dispatcher.dispatch_complete",
            new=AsyncMock(side_effect=RuntimeError("provider down")),
        ):
            result = await _build_sdxl_prompt(
                "GPU benchmarks", "llama3:latest", site_config=site_config,
            )
        assert "photorealistic scene related to GPU benchmarks" in result

    # poindexter#716 — model=None paths

    async def test_no_model_no_pool_returns_fallback_prompt(self):
        """poindexter#716: model=None + no pool → generic fallback (no LLM call)."""
        with patch("httpx.AsyncClient", side_effect=AssertionError("must not use httpx")):
            result = await _build_sdxl_prompt("AI chip design", None, site_config=None)
        assert "photorealistic scene related to AI chip design" in result

    async def test_no_model_resolves_via_tier(self):
        """poindexter#716: model=None + pool → resolve_tier_model, then dispatch."""
        site_config = MagicMock()
        pool = MagicMock()
        site_config._pool = pool

        completion = MagicMock()
        completion.text = '"a cinematic AI chip scene, neon lighting, 4k"'

        with patch(
            "services.llm_providers.dispatcher.resolve_tier_model",
            new=AsyncMock(return_value="ollama/gemma3:27b"),
        ) as mock_resolve, patch(
            "services.llm_providers.dispatcher.dispatch_complete",
            new=AsyncMock(return_value=completion),
        ) as mock_dispatch:
            result = await _build_sdxl_prompt(
                "AI chip design", None, site_config=site_config,
            )

        mock_resolve.assert_awaited_once_with(pool, "standard")
        # dispatch_complete must receive the resolved model, not a hardcoded name.
        assert mock_dispatch.await_args is not None
        assert mock_dispatch.await_args.kwargs["model"] == "ollama/gemma3:27b"
        assert "AI chip scene" in result

    async def test_no_model_resolve_failure_returns_fallback(self):
        """poindexter#716: resolve_tier_model failure → fallback prompt, no crash."""
        site_config = MagicMock()
        site_config._pool = MagicMock()

        with patch(
            "services.llm_providers.dispatcher.resolve_tier_model",
            new=AsyncMock(side_effect=RuntimeError("no tier mapping")),
        ):
            result = await _build_sdxl_prompt(
                "Robotics", None, site_config=site_config,
            )
        assert "photorealistic scene related to Robotics" in result
