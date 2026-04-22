"""AIGenerationProvider — LLM-crafted prompt → SDXL (or other generator).

Phase G follow-up (GitHub #71). One layer above ``SdxlProvider``: takes a
BLOG TOPIC (not a pre-baked SDXL prompt), asks an LLM to write a tailored
SDXL prompt, then delegates to the configured generator.

Today's implementation hardcodes SDXL as the backend; the ``generator``
config knob exists so a future Flux/DALL-E provider can swap in without
code change.

Config (``plugin.image_provider.ai_generation`` in app_settings):

- ``enabled`` (default true)
- ``config.prompt_model`` (default ``"llama3:latest"``) — Ollama model
  used to write SDXL prompts
- ``config.generator`` (default ``"sdxl"``) — which image provider to
  delegate to
- All ``SdxlProvider`` config keys are forwarded when ``generator="sdxl"``

Kind: ``"generate"``.
"""

from __future__ import annotations

import logging
from typing import Any

from plugins.image_provider import ImageResult

logger = logging.getLogger(__name__)


class AIGenerationProvider:
    """Wrap a raw image generator with an LLM-driven prompt step."""

    name = "ai_generation"
    kind = "generate"

    async def fetch(
        self,
        query_or_prompt: str,
        config: dict[str, Any],
    ) -> list[ImageResult]:
        topic = (query_or_prompt or "").strip()
        if not topic:
            return []

        prompt_model = str(config.get("prompt_model", "llama3:latest"))
        generator_name = str(config.get("generator", "sdxl") or "sdxl")

        # Phase H step 4.5 (GH#95): resolve site_config from the dispatcher's
        # reserved ``_site_config`` key. Transitional fallback removed in step 5.
        _sc = config.get("_site_config")
        if _sc is None:
            try:
                from services.site_config import site_config as _sc
            except Exception:
                _sc = None

        sdxl_prompt = await _build_sdxl_prompt(topic, prompt_model, _sc)

        # Resolve the downstream provider. Stay inside the plugin registry
        # so swapping to flux/dalle/etc. later is a config change.
        from plugins.registry import get_image_providers
        providers = {p.name: p for p in get_image_providers()}
        target = providers.get(generator_name)
        if target is None:
            logger.warning(
                "[AIGeneration] generator %r not registered; falling back to sdxl",
                generator_name,
            )
            target = providers.get("sdxl")
            if target is None:
                logger.error("[AIGeneration] no sdxl provider registered either")
                return []

        # Strip our prompt_model + generator keys before forwarding so the
        # downstream provider doesn't choke on unknown config.
        forward_config = {
            k: v
            for k, v in config.items()
            if k not in {"prompt_model", "generator"}
        }
        results = await target.fetch(sdxl_prompt, forward_config)

        # Re-label with our name so upstream callers know which provider
        # did the work end-to-end; keep the downstream source in metadata
        # so the trail is preserved.
        relabelled: list[ImageResult] = []
        for r in results:
            r.source = self.name
            r.metadata = {**r.metadata, "downstream_generator": target.name, "topic": topic}
            r.search_query = topic
            relabelled.append(r)
        return relabelled


async def _build_sdxl_prompt(
    topic: str, model: str, site_config: Any = None,
) -> str:
    """Ask Ollama to write a tailored SDXL prompt. Fall back to a
    generic photorealistic template when Ollama is unreachable.

    Shared shape with services/jobs/regenerate_stock_images.py — kept in
    sync so both the Job and the Provider produce similar output.
    """
    if site_config is None:
        from services.site_config import site_config

    fallback = (
        f"photorealistic scene related to {topic[:50]}, cinematic lighting, "
        f"4k, detailed, no people, no text"
    )
    try:
        import httpx
        ollama = site_config.get("ollama_base_url", "http://host.docker.internal:11434")
        async with httpx.AsyncClient(timeout=30) as client:
            resp = await client.post(
                f"{ollama}/api/generate",
                json={
                    "model": model,
                    "prompt": (
                        f"Write a Stable Diffusion XL prompt for a blog featured "
                        f"image about: {topic[:80]}\n"
                        f"Requirements: photorealistic scene, cinematic lighting, "
                        f"no people, no text. 1 sentence only. Output ONLY the prompt."
                    ),
                    "stream": False,
                    "options": {"num_predict": 100, "temperature": 0.7},
                },
            )
            resp.raise_for_status()
            generated = resp.json().get("response", "").strip().strip('"')
            if len(generated) > 20:
                return generated
    except Exception as e:
        logger.debug("[AIGeneration] prompt synth failed (using fallback): %s", e)
    return fallback
