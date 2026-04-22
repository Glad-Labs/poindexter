"""
Unified Image Service — Phase G thin dispatcher.

Post-Phase-G (GH#71) responsibility split:

- ``ImageService.generate_image`` dispatches to the
  ``plugin.image_provider.primary`` provider registered in
  ``plugins.registry`` (default ``sdxl``). The SDXL model lifecycle,
  host sidecar HTTP call, and upload paths live in
  ``services/image_providers/sdxl.py``.
- ``ImageService.search_featured_image`` and
  ``ImageService.get_images_for_gallery`` still handle Pexels
  orchestration (semantic-query LLM preprocessing, concept fallbacks,
  random selection). The raw HTTP search is delegated to
  ``services/image_providers/pexels.py`` — rewired in Phase G step 3.
- ``FeaturedImageMetadata`` remains the public dataclass callers import.

The SDXL model registry (``ImageModel`` enum, ``ImageModelConfig``,
``IMAGE_MODEL_REGISTRY``, ``get_default_image_model``) is re-exported
from ``services/image_providers/_sdxl_models.py`` so existing callers
and test patches keep working unchanged.

Supported models (see ``_sdxl_models.py``):
- sdxl_base: stabilityai/stable-diffusion-xl-base-1.0 (30 steps, ~6GB VRAM)
- sdxl_lightning: SDXL base + ByteDance Lightning LoRA (4 steps, ~6GB VRAM)
- flux_schnell: black-forest-labs/FLUX.1-schnell (4 steps, ~12GB VRAM)

Cost: $0/month for all options (local GPU or CPU fallback).
"""

import os
from datetime import datetime, timezone
from typing import Any

from plugins.image_provider import ImageResult
from services.logger_config import get_logger

# SDXL model registry + torch availability probes live in a shared module
# so ``services/image_providers/sdxl.py`` can own the model lifecycle
# without routing back through image_service. Re-exported here for
# backward compatibility: existing callers import
# ``ImageModel`` / ``IMAGE_MODEL_REGISTRY`` / ``get_default_image_model``
# from ``services.image_service``, and tests patch
# ``services.image_service.TORCH_AVAILABLE`` etc.
from services.image_providers._sdxl_models import (
    DIFFUSERS_AVAILABLE,
    IMAGE_MODEL_REGISTRY,
    TORCH_AVAILABLE,
    XFORMERS_AVAILABLE,
    ImageModel,
    ImageModelConfig,
    get_default_image_model,
    torch,
)

__all__ = [
    "DIFFUSERS_AVAILABLE",
    "FeaturedImageMetadata",
    "IMAGE_MODEL_REGISTRY",
    "ImageModel",
    "ImageModelConfig",
    "ImageService",
    "TORCH_AVAILABLE",
    "XFORMERS_AVAILABLE",
    "get_default_image_model",
    "get_image_service",
    "torch",
]


logger = get_logger(__name__)


class FeaturedImageMetadata:
    """Metadata for a featured image"""

    def __init__(
        self,
        url: str,
        thumbnail: str | None = None,
        photographer: str = "Unknown",
        photographer_url: str = "",
        width: int | None = None,
        height: int | None = None,
        alt_text: str = "",
        caption: str = "",
        source: str = "pexels",
        search_query: str = "",
    ):
        self.url = url
        self.thumbnail = thumbnail or url
        self.photographer = photographer
        self.photographer_url = photographer_url
        self.width = width
        self.height = height
        self.alt_text = alt_text
        self.caption = caption
        self.source = source
        self.search_query = search_query
        self.retrieved_at = datetime.now(timezone.utc)

    def to_dict(self) -> dict[str, Any]:
        """Convert to dictionary for database storage"""
        return {
            "url": self.url,
            "thumbnail": self.thumbnail,
            "photographer": self.photographer,
            "photographer_url": self.photographer_url,
            "width": self.width,
            "height": self.height,
            "alt_text": self.alt_text,
            "caption": self.caption,
            "source": self.source,
            "search_query": self.search_query,
            "retrieved_at": self.retrieved_at.isoformat(),
        }

    def to_markdown(self, caption_override: str | None = None) -> str:
        """Generate markdown with photographer attribution"""
        caption = caption_override or self.caption or self.alt_text or "Featured Image"

        photographer_link = self.photographer
        if self.photographer_url:
            photographer_link = f"[{self.photographer}]({self.photographer_url})"

        return f"""![{caption}]({self.url})
*Photo by {photographer_link} on {self.source.capitalize()}*"""


def _image_result_to_metadata(r: ImageResult) -> FeaturedImageMetadata:
    """Adapt ImageResult (provider Protocol) to FeaturedImageMetadata.

    ImageProviders return the generic ``ImageResult`` dataclass;
    existing callers of ``ImageService.search_featured_image`` still
    expect the legacy ``FeaturedImageMetadata`` — this keeps the public
    surface stable during the Pexels-through-provider cutover.
    """
    return FeaturedImageMetadata(
        url=r.url,
        thumbnail=r.thumbnail,
        photographer=r.photographer,
        photographer_url=r.photographer_url,
        width=r.width,
        height=r.height,
        alt_text=r.alt_text,
        caption=r.caption,
        source=r.source or "pexels",
        search_query=r.search_query,
    )


def _resolve_image_provider(name: str) -> Any | None:
    """Look up a registered ImageProvider by name.

    Core providers ship via ``plugins.registry.get_core_samples()``
    while third-party providers register through entry_points and are
    exposed via ``get_image_providers()``. Check both sources so a
    community plugin can override a core provider by registering under
    the same name.
    """
    try:
        from plugins.registry import get_core_samples, get_image_providers
    except Exception as e:
        logger.warning("image provider registry unavailable: %s", e)
        return None

    providers: list[Any] = []
    try:
        providers.extend(get_image_providers())
    except Exception as e:
        logger.debug("get_image_providers failed: %s", e)
    try:
        providers.extend(get_core_samples().get("image_providers", []))
    except Exception as e:
        logger.debug("get_core_samples failed: %s", e)

    for provider in providers:
        if getattr(provider, "name", None) == name:
            return provider
    return None


class ImageService:
    """Unified service for Pexels search + ImageProvider dispatch.

    Featured-image search orchestration (semantic-query preprocessing,
    concept fallbacks, random result selection) stays on this service.
    Image generation is dispatched to the configured ImageProvider —
    ``plugin.image_provider.primary`` in app_settings, defaults to
    ``sdxl``. See ``services/image_providers/sdxl.py`` for the full
    SDXL lifecycle.
    """

    def __init__(self):
        """Initialize image service.

        Pexels API key resolution lives inside ``PexelsProvider`` now —
        we only cache the "is Pexels configured at all?" verdict here
        so ``search_featured_image`` can short-circuit the expensive
        LLM semantic-query path when there's no key to hit Pexels with.
        """
        self._pexels_key_checked_db = False
        self.pexels_available = False
        self.search_cache: dict[str, list[FeaturedImageMetadata]] = {}

    # ------------------------------------------------------------------
    # SDXL provider state shims
    #
    # ``services/stages/source_featured_image.py`` checks
    # ``image_service.sdxl_available`` and ``sdxl_initialized`` to
    # decide whether to attempt SDXL before falling back to Pexels.
    # Post-Phase-G those flags live on the SdxlProvider's module-level
    # state — expose them as properties here so existing callers keep
    # working.
    # ------------------------------------------------------------------

    @property
    def sdxl_available(self) -> bool:
        """True when the in-process diffusers pipeline has loaded.

        False when either diffusers hasn't been initialized yet OR the
        init failed. The host SDXL sidecar path is independent of this
        flag — the sidecar may be reachable and serve images even when
        in-process diffusers isn't available.
        """
        try:
            from services.image_providers.sdxl import _state as _sdxl_state
        except Exception:
            return False
        return bool(getattr(_sdxl_state, "available", False))

    @property
    def sdxl_initialized(self) -> bool:
        """True once the in-process diffusers init path has been attempted."""
        try:
            from services.image_providers.sdxl import _state as _sdxl_state
        except Exception:
            return False
        return bool(getattr(_sdxl_state, "initialized", False))

    # =========================================================================
    # DB-FIRST KEY PROBE
    # =========================================================================

    async def _ensure_pexels_key(self) -> None:
        """Probe whether ``pexels_api_key`` is configured.

        Post-Phase-G the PexelsProvider reads the key itself on every
        ``fetch()`` call — this method exists only to cache the
        is-it-configured verdict on the service instance so
        ``search_featured_image`` can short-circuit its LLM semantic-
        query branch when Pexels isn't set up at all.

        Cached per service instance — subsequent calls are no-ops once
        the probe has run.
        """
        if self._pexels_key_checked_db:
            return

        from plugins.secrets import get_secret
        from services.container import get_service

        db_service = get_service("database")
        if db_service is None or not getattr(db_service, "pool", None):
            logger.warning(
                "DatabaseService not registered in DI container — "
                "pexels_api_key probe skipped. Callers will see "
                "pexels_available=False.",
            )
            self._pexels_key_checked_db = True
            return

        async with db_service.pool.acquire() as conn:
            value = await get_secret(conn, "pexels_api_key")

        self._pexels_key_checked_db = True
        self.pexels_available = bool(value)
        if value:
            logger.info("pexels_api_key present in app_settings")
        else:
            logger.warning("pexels_api_key not set in app_settings")

    # =========================================================================
    # FEATURED IMAGE SEARCH (Pexels - Free, Unlimited)
    # =========================================================================

    async def _llm_semantic_pexels_query(self, topic: str) -> str | None:
        """Ask the LLM for a Pexels-friendly semantic query.

        The raw topic is often a literal keyword match in Pexels that
        produces terrible featured images: "DuckDB vs Postgres" returns
        a photo of an actual duck, "Kubernetes pod lifecycle" returns a
        pea pod, etc. This converts the topic into a concept-level query
        ("data analytics dashboard", "container orchestration workspace")
        that retrieves professional stock photos instead of literal
        keyword matches.

        Returns None if Ollama is unavailable or the response is empty
        — callers fall back to the raw topic.
        """
        # Migrated v2.2: use the Provider Protocol rather than constructing
        # OllamaClient directly. Keeps image_service swap-able across
        # local inference backends (Ollama, vllm, llama.cpp) by
        # changing ``plugin.llm_provider.primary.free`` in app_settings.
        try:
            import asyncio

            from plugins.registry import get_llm_providers
        except Exception:
            return None

        # Tuning constants via app_settings (#198).
        from services.site_config import site_config as _sc
        _client_timeout = _sc.get_int("image_ollama_client_timeout_seconds", 30)
        _model = _sc.get("image_search_query_model", "gemma3:27b")
        _max_tokens = _sc.get_int("image_search_query_max_tokens", 30)
        _temp = _sc.get_float("image_search_query_temperature", 0.4)
        _generate_timeout = _sc.get_int("image_search_query_timeout_seconds", 20)

        providers = {p.name: p for p in get_llm_providers()}
        provider = providers.get("ollama_native")
        if provider is None:
            logger.debug(
                "LLM semantic query: ollama_native provider not registered",
            )
            return None

        prompt = (
            "Convert this blog topic into a 3-5 word Pexels stock photo "
            "search query that represents the CONCEPT or ABSTRACT IDEA, "
            "NOT the literal words. Avoid brand names, product names, and "
            "technical jargon — Pexels doesn't have photos of software.\n\n"
            "Focus on what the reader cares about: the work being done, "
            "the problem being solved, the emotion involved, or the "
            "industry context.\n\n"
            "Examples:\n"
            "- Topic: 'Postgres row-level security for multi-tenant SaaS'\n"
            "  Query: secure database architecture\n"
            "- Topic: 'When to choose DuckDB over Postgres for analytics'\n"
            "  Query: data analytics dashboard\n"
            "- Topic: 'Building a FastAPI background task queue'\n"
            "  Query: server room infrastructure\n"
            "- Topic: 'Why local LLMs beat cloud APIs for indie hackers'\n"
            "  Query: modern developer workspace\n"
            "- Topic: 'Kubernetes pod lifecycle debugging'\n"
            "  Query: data center network cables\n\n"
            f"Topic: {topic}\n\n"
            "Respond with ONLY the search query "
            "(3-5 words, no quotes, no explanation):"
        )

        try:
            completion = await asyncio.wait_for(
                provider.complete(
                    messages=[{"role": "user", "content": prompt}],
                    model=_model,
                    temperature=_temp,
                    max_tokens=_max_tokens,
                    timeout_s=_client_timeout,
                ),
                timeout=_generate_timeout,
            )
            text = (completion.text or "").strip()
            # Strip common LLM quote/markdown wrappers
            text = text.strip('"').strip("'").strip("`").strip()
            # Take first non-empty line (models sometimes add an empty trailer)
            for line in text.split("\n"):
                line = line.strip().strip('"').strip("'").strip()
                if line and 3 <= len(line) <= 80:
                    return line
        except Exception as e:
            logger.debug(
                "LLM semantic query failed for '%s': %s", topic[:40], e,
            )
        return None

    async def search_featured_image(
        self,
        topic: str,
        keywords: list[str] | None = None,
        orientation: str = "landscape",
        size: str = "medium",
        page: int = 1,
    ) -> FeaturedImageMetadata | None:
        """
        Search for featured image using Pexels API.

        Args:
            topic: Main search topic
            keywords: Additional keywords to try if topic search fails
            orientation: Image orientation (landscape, portrait, square)
            size: Image size (small, medium, large)
            page: Results page number for pagination (default 1, use
                higher for different results)

        Returns:
            FeaturedImageMetadata or None if no image found
        """
        import random

        await self._ensure_pexels_key()

        if not self.pexels_available:
            logger.warning("Pexels API key not configured (checked env + DB)")
            return None

        # Build search queries, prioritizing a concept-level query over
        # the raw topic. An LLM preprocessing step converts topics like
        # "DuckDB vs Postgres for analytics" into "data analytics
        # dashboard" so Pexels returns relevant stock photos instead of
        # matching on "duck" the animal.
        #
        # The semantic preprocessing is SKIPPED for short/fragmented
        # strings because the inline-image pipeline calls
        # search_featured_image() with alt-text snippets like
        # "A close-up image of a" which aren't real topics. Running the
        # LLM on those just burns 2s of inference per inline image
        # (3-4 per post) for no semantic benefit. Heuristic: only
        # preprocess if the string is long enough to be a real topic.
        search_queries: list[str] = []
        _topic_words = len((topic or "").split())
        _looks_like_real_topic = (
            topic
            and len(topic) >= 25
            and _topic_words >= 4
            and not topic.lower().startswith(("a ", "an ", "the "))
        )
        if _looks_like_real_topic:
            semantic_query = await self._llm_semantic_pexels_query(topic)
            if semantic_query:
                search_queries.append(semantic_query)
                logger.info(
                    "[FEATURED] Using semantic Pexels query: '%s' (from topic '%s')",
                    semantic_query, topic[:60],
                )
        # Always include the raw topic as a fallback — if the semantic
        # query returns zero results (or was skipped for fragmented
        # alt-text), the raw topic might still hit something.
        search_queries.append(topic)

        # Concept-based fallbacks (no people)
        concept_keywords = [
            "technology", "digital", "abstract", "modern", "innovation",
            "data", "network", "background", "desktop", "workspace",
            "object", "product", "design", "pattern", "texture",
            "nature", "landscape", "environment", "system", "interface",
        ]

        # Add user keywords but avoid person/people related terms
        if keywords:
            for kw in keywords[:3]:
                if not any(
                    term in kw.lower()
                    for term in ["person", "people", "portrait", "face", "human"]
                ):
                    search_queries.append(kw)

        search_queries.append(f"{topic} technology")
        search_queries.append(f"{topic} abstract")
        search_queries.extend(concept_keywords[:2])

        for query in search_queries:
            try:
                logger.info("Searching Pexels for: '%s' (page %s)", query, page)
                images = await self._pexels_search(
                    query, per_page=5, orientation=orientation,
                    size=size, page=page,
                )
                if images:
                    # Pick random image from results instead of always the
                    # first — prevents similar topics from all landing on
                    # the same photo.
                    metadata = random.choice(images)
                    logger.info(
                        "Found featured image for '%s' using query '%s' "
                        "(page %s) - randomly selected from %s results",
                        topic, query, page, len(images),
                    )
                    return metadata
            except Exception as e:
                logger.warning(
                    "Error searching for '%s': %s", query, e, exc_info=True,
                )

        logger.warning("No featured image found for topic: %s", topic)
        return None

    async def get_images_for_gallery(
        self,
        topic: str,
        count: int = 5,
        keywords: list[str] | None = None,
    ) -> list[FeaturedImageMetadata]:
        """
        Get multiple images for content gallery.

        Args:
            topic: Gallery search topic
            count: Number of images needed
            keywords: Additional keywords

        Returns:
            List of FeaturedImageMetadata objects
        """
        await self._ensure_pexels_key()

        if not self.pexels_available:
            logger.warning("Pexels API key not configured (checked env + DB)")
            return []

        search_queries = [topic]
        if keywords:
            search_queries.extend(keywords)

        all_images: list[FeaturedImageMetadata] = []

        for query in search_queries[:3]:  # Try up to 3 queries
            try:
                images = await self._pexels_search(query, per_page=count)
                all_images.extend(images)

                if len(all_images) >= count:
                    logger.info("Found %s gallery images", len(all_images))
                    return all_images[:count]

            except Exception as e:
                logger.warning(
                    "Error searching for gallery images '%s': %s",
                    query, e, exc_info=True,
                )

        logger.info(
            "Found %s gallery images (less than requested)", len(all_images),
        )
        return all_images

    async def _pexels_search(
        self,
        query: str,
        per_page: int = 5,
        orientation: str = "landscape",
        size: str = "medium",
        page: int = 1,
    ) -> list[FeaturedImageMetadata]:
        """Delegate a Pexels search to the registered PexelsProvider.

        The provider handles the HTTP call, API-key resolution, and
        rate-limit fallbacks. We adapt its ``ImageResult`` list back
        to the legacy ``FeaturedImageMetadata`` type so existing
        callers (and the random-selection orchestration above) keep
        working unchanged.
        """
        provider = _resolve_image_provider("pexels")
        if provider is None:
            logger.debug(
                "PexelsProvider not registered - skipping search for '%s'", query,
            )
            return []

        try:
            results = await provider.fetch(
                query,
                {
                    "per_page": per_page,
                    "orientation": orientation,
                    "size": size,
                    "page": page,
                },
            )
        except Exception as e:
            logger.error("Pexels search error: %s", e, exc_info=True)
            return []

        return [_image_result_to_metadata(r) for r in results]

    # =========================================================================
    # IMAGE GENERATION DISPATCH (ImageProvider plugin registry)
    # =========================================================================

    async def generate_image(
        self,
        prompt: str,
        output_path: str,
        negative_prompt: str | None = None,
        num_inference_steps: int | None = None,
        guidance_scale: float | None = None,
        task_id: str | None = None,
        model: ImageModel | None = None,
    ) -> bool:
        """Dispatch to the configured ImageProvider (default: SDXL).

        Reads ``plugin.image_provider.primary`` from site_config to pick
        a provider, looks it up via ``plugins.registry``, and forwards
        the generation knobs through the provider's ``config`` dict.
        Returns True when the provider yields at least one ImageResult
        AND the PNG is present at ``output_path``.

        Pre-Phase-G this method held the full SDXL model lifecycle; it's
        now a thin dispatcher. See ``services/image_providers/sdxl.py``
        for the sidecar + in-process diffusers implementation.

        Args:
            prompt: Image generation prompt
            output_path: Local path to save generated image
            negative_prompt: Negative prompt for quality improvement
            num_inference_steps: Override inference steps
                (defaults to model config)
            guidance_scale: Override guidance scale
                (defaults to model config)
            task_id: Optional task ID for progress tracking via WebSocket
            model: Which model to use (defaults to site_config.image_model
                or sdxl_lightning)

        Returns:
            True if successful, False otherwise.
        """
        from services.site_config import site_config

        provider_name = site_config.get("plugin.image_provider.primary", "sdxl")
        provider = _resolve_image_provider(provider_name)
        if provider is None:
            logger.warning(
                "Image provider %r not registered; generation skipped",
                provider_name,
            )
            return False

        config: dict[str, Any] = {
            "output_path": output_path,
            "negative_prompt": negative_prompt or "",
        }
        if num_inference_steps is not None:
            config["num_inference_steps"] = num_inference_steps
        if guidance_scale is not None:
            config["guidance_scale"] = guidance_scale
        if task_id is not None:
            config["task_id"] = task_id
        if model is not None:
            config["model"] = model.value

        try:
            results = await provider.fetch(prompt, config)
        except Exception as e:
            logger.error(
                "Image provider %r raised: %s", provider_name, e, exc_info=True,
            )
            return False

        return bool(results) and os.path.exists(output_path)

    # =========================================================================
    # UTILITY METHODS
    # =========================================================================

    def generate_image_markdown(
        self,
        image: FeaturedImageMetadata,
        caption: str | None = None,
    ) -> str:
        """Generate markdown for image with attribution"""
        return image.to_markdown(caption)

    async def optimize_image_for_web(
        self,
        image_url: str,
        max_width: int = 1200,  # noqa: ARG002 — placeholder, honored once impl lands  # pyright: ignore[reportUnusedParameter]
        max_height: int = 630,  # noqa: ARG002 — same  # pyright: ignore[reportUnusedParameter]
    ) -> dict[str, Any] | None:
        """
        Optimize image for web delivery.

        Args:
            image_url: URL of image to optimize
            max_width: Maximum width
            max_height: Maximum height

        Returns:
            Optimization result dict or None
        """
        # Placeholder for future image optimization — could integrate
        # with imgix, Cloudinary, or local pillow pipeline.
        logger.warning(
            "[image_service] optimize_image called but not implemented — "
            "returning unoptimized",
        )
        logger.info("Image optimization placeholder for %s", image_url)
        return {
            "url": image_url,
            "optimized": False,
            "note": "Image optimization not yet implemented",
        }

    def get_search_cache(self, query: str) -> list[FeaturedImageMetadata] | None:
        """Get cached search results"""
        return self.search_cache.get(query)

    def set_search_cache(
        self,
        query: str,
        results: list[FeaturedImageMetadata],
    ) -> None:
        """Cache search results (24-hour TTL in production)"""
        self.search_cache[query] = results


def get_image_service() -> ImageService:
    """Factory function for dependency injection"""
    return ImageService()
