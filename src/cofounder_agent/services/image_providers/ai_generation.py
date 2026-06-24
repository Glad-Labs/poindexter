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
from typing import TYPE_CHECKING, Any

from plugins.image_provider import ImageResult

if TYPE_CHECKING:
    import httpx

logger = logging.getLogger(__name__)


# Lifespan-bound shared httpx.AsyncClient — main.py wires this via
# set_http_client() at startup. ``_build_sdxl_prompt`` prefers it so
# the Ollama connection pool stays warm across per-task image
# generations.
http_client: httpx.AsyncClient | None = None


def set_http_client(client: httpx.AsyncClient | None) -> None:
    """Wire the lifespan-bound shared httpx.AsyncClient."""
    global http_client
    http_client = client


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

        # poindexter#716 — only honour an explicit plugin-config override; pass
        # None when the key is missing or set to "auto" so _build_sdxl_prompt
        # can resolve via the cost-tier router instead of pinning a literal.
        _explicit = config.get("prompt_model") or ""
        prompt_model: str | None = (
            str(_explicit) if (_explicit and _explicit != "auto") else None
        )
        generator_name = str(config.get("generator", "sdxl") or "sdxl")

        # DI seam (glad-labs-stack#330) — image_provider plugins receive
        # `_site_config` from the dispatcher per CLAUDE.md.
        sdxl_prompt = await _build_sdxl_prompt(
            topic, prompt_model, site_config=config.get("_site_config"),
        )

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


_HUMAN_TERM_RE = None


def _scrub_human_terms(prompt: str) -> tuple[str, bool]:
    """Strip anthropomorphic terms from an SDXL POSITIVE prompt (#522).

    Faces/hands/people are the worst AI image tells, and a human named in the
    positive prompt slips past the negative prompt (it's central to the scene
    SDXL is asked to render). We remove those terms at build time so the
    builder never emits a human-centric scene. Returns
    ``(cleaned_prompt, had_human_terms)``.
    """
    import re

    global _HUMAN_TERM_RE
    if _HUMAN_TERM_RE is None:
        _HUMAN_TERM_RE = re.compile(
            r"\b(?:people|persons?|humans?|men|women|man|woman|child(?:ren)?|"
            r"kid|boy|girl|developer|engineer|programmer|coder|worker|team|"
            r"crowd|figures?|hands?|fingers?|faces?|portrait|selfie|posing|"
            r"standing|sitting|walking)\b",
            re.IGNORECASE,
        )
    if not _HUMAN_TERM_RE.search(prompt):
        return prompt, False
    cleaned = _HUMAN_TERM_RE.sub("", prompt)
    cleaned = re.sub(r"\s{2,}", " ", cleaned)
    cleaned = re.sub(r"\s+,", ",", cleaned)
    cleaned = re.sub(r"(?:,\s*){2,}", ", ", cleaned).strip(" ,")
    return cleaned, True


async def _build_sdxl_prompt(
    topic: str, model: str | None, *, site_config: Any = None,
) -> str:
    """Ask the configured LLM to write a tailored SDXL prompt. Fall back
    to a generic stylistic or abstract template when the model is unreachable.

    ``model`` may be ``None`` when the caller has no explicit override — in
    that case the pool is used to resolve the "standard" cost tier (poindexter
    #716). If neither a model nor a pool is available the generic fallback
    prompt is returned immediately.

    Routes through :func:`services.llm_providers.dispatcher.dispatch_complete`
    when an asyncpg pool is reachable via the ``site_config._pool`` DI seam
    (production / worker path — picks up the provider configured by
    ``plugin.llm_provider.primary.<tier>`` and lands the call in cost
    accounting + Langfuse). Falls back to a direct ``httpx`` POST to local
    Ollama's ``/api/generate`` when no pool is wired (unit tests / bootstrap).
    This retires the last direct-httpx Ollama caller in the image-provider
    path (Glad-Labs/poindexter#535) — the same dispatcher-or-httpx shape
    already used by ``topic_ranking._ollama_chat_json`` and
    ``llm_text.ollama_chat_text``.

    Human/anthropomorphic terms are scrubbed from the result (#522): they
    belong in the NEGATIVE prompt, not the positive — putting "no people" in
    the positive backfires (SDXL tokenizes "people"), and any human the LLM
    injects gets stripped so it can't anchor the scene.
    """
    # NOTE: exclusion lives in the negative prompt; the positive describes the
    # scene as unpopulated objects/environment only (no "no people" token).
    fallback = (
        f"stylistic or abstract scene related to {topic[:50]}, cinematic lighting, "
        f"4k, detailed, objects and environment only, unpopulated"
    )
    instruction = (
        f"Write a Stable Diffusion XL prompt for a blog featured "
        f"image about: {topic[:80]}\n"
        f"Requirements: stylistic or abstract scene, cinematic lighting. "
        f"Depict objects, technology, landscapes, or abstract concepts "
        f"ONLY — absolutely no people, no humans, no faces, no hands. "
        f"1 sentence only. Output ONLY the prompt."
    )

    pool = getattr(site_config, "_pool", None) if site_config is not None else None

    # When no explicit model was provided, read the per-step pin
    # (sdxl_prompt_model). Empty → return the generic fallback prompt rather
    # than sending an empty-model request.
    if not model:
        model = (
            (site_config.get("sdxl_prompt_model") or "").strip()
            if site_config is not None
            else ""
        )
        if not model:
            logger.debug(
                "[AIGeneration] no explicit model and sdxl_prompt_model unset; "
                "using fallback prompt",
            )
            return fallback

    # At this point model is guaranteed non-None: it was either passed
    # explicitly, resolved from the ``sdxl_prompt_model`` pin above, or the
    # function already returned the fallback prompt.
    assert model is not None

    try:
        if pool is not None:
            # Production path — dispatch through the configured LLM provider
            # so provider-swappability, cost tracking, retries, and Langfuse
            # tracing apply uniformly (poindexter#535).
            from services.llm_providers.dispatcher import dispatch_complete

            completion = await dispatch_complete(
                pool=pool,
                messages=[{"role": "user", "content": instruction}],
                model=model,
                tier="standard",
                phase="ai_generation.sdxl_prompt",
                timeout_s=30,
                temperature=0.7,
                max_tokens=100,
            )
            generated = (getattr(completion, "text", "") or "").strip().strip('"')
        else:
            # Test / bootstrap fallback — direct httpx → local Ollama. Same
            # wire shape as before the dispatcher cutover.
            import httpx
            ollama = (
                site_config.get("ollama_base_url", "http://host.docker.internal:11434")
                if site_config is not None
                else "http://host.docker.internal:11434"
            )
            _body = {
                "model": model,
                "prompt": instruction,
                "stream": False,
                "options": {"num_predict": 100, "temperature": 0.7},
            }
            if http_client is not None:
                resp = await http_client.post(
                    f"{ollama}/api/generate", json=_body, timeout=30,
                )
            else:
                async with httpx.AsyncClient(timeout=30) as client:
                    resp = await client.post(
                        f"{ollama}/api/generate", json=_body,
                    )
            resp.raise_for_status()
            generated = resp.json().get("response", "").strip().strip('"')
        if len(generated) > 20:
            cleaned, had_humans = _scrub_human_terms(generated)
            if had_humans:
                logger.info(
                    "[AIGeneration] scrubbed human/anthropomorphic terms from "
                    "generated SDXL prompt (#522)"
                )
            # Only use the cleaned prompt if scrubbing didn't gut it.
            if len(cleaned) >= 20:
                return cleaned
    except Exception as e:
        logger.debug("[AIGeneration] prompt synth failed (using fallback): %s", e)
    return fallback
