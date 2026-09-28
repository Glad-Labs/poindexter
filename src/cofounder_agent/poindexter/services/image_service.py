"""
Unified Image Service

Consolidates all image processing functionality:
- Featured image sourcing (Pexels API - free, unlimited)
- Image generation, rendered by the image-gen HTTP server
- Gallery image sourcing
- Metadata generation and photographer attribution

Architecture:
- All operations are async (httpx for Pexels and for the image-gen server)
- Generation POSTs to the image-gen HTTP server (``scripts/image-gen-server.py``,
  its own CUDA container) under ``gpu.lock("image_gen")``. The server picks the
  model from ``app_settings.image_generation_model``, lazy-loads it on the
  first request and unloads it when idle. This module loads no model and
  imports no torch or diffusers.
- A failed render returns an ``ImageGenOutcome`` naming the reason (the
  server's own diagnosis, where it gave one) instead of a bare ``False``
- Automatic photographer attribution from Pexels

Model choice: the image-gen server's ``REGISTRY`` is the only list of models
that can render, and ``app_settings.image_generation_model`` picks one. The
worker keeps no model registry of its own. ``ImageModel`` survives here only as
the type of the deprecated, ignored ``model=`` parameter.

Cost: $0/month (Pexels free tier; generation on the local GPU)
"""

import asyncio
from dataclasses import dataclass
from datetime import datetime, timezone
from enum import Enum
from typing import Any

import httpx

from poindexter.services import live_activity
from poindexter.services.logger_config import get_logger
from poindexter.services.site_config import SiteConfig
from poindexter.utils.exception_format import describe_exception

# SiteConfig DI (#272 Phase-2e): the module-level ``site_config`` global +
# ``set_site_config`` setter were removed. Injection is mandatory:
# ``ImageService`` takes a required ``site_config=`` ctor kwarg. Callers thread
# the run-bound instance (the ``get_image_service(site_config=...)`` factory,
# pipeline stages via ``context.get("site_config")``, jobs/providers via
# ``config["_site_config"]``).


# Lifespan-bound shared httpx.AsyncClient — main.py wires this via
# set_http_client() at startup. The Pexels search + image-gen generation
# paths prefer it so the per-task connection pool is reused.
http_client: "httpx.AsyncClient | None" = None


def set_http_client(client: "httpx.AsyncClient | None") -> None:
    """Wire the lifespan-bound shared httpx.AsyncClient."""
    global http_client
    http_client = client


def _write_image_bytes(path: str, content: bytes) -> None:
    """Sync helper for ``asyncio.to_thread`` — writes image-gen response to disk.

    image-gen images are 1–5 MB on typical prompts; a blocking ``open()`` at
    that size would stall the event loop for the duration of the write
    under concurrent load (ASYNC230).
    """
    with open(path, "wb") as f:
        f.write(content)


def _server_error_detail(resp: Any) -> str:
    """Best line of explanation available from an image-gen error response.

    FastAPI puts the real cause in ``{"detail": "..."}`` — that is where
    ``scripts/image-gen-server.py`` writes "CUDA out of memory. Tried to
    allocate 76.00 MiB…", "image-gen server degraded: <reason>" and "GPU OOM".
    Falls back to the raw body when it isn't JSON-shaped, and truncates
    because a torch OOM message carries a multi-line allocator dump the
    operator does not need in an HTTP error body.
    """
    try:
        payload = resp.json()
        if isinstance(payload, dict):
            detail = payload.get("detail") or payload.get("error")
            if detail:
                return str(detail)[:400].strip()
    except Exception:  # noqa: BLE001  # silent-ok: a non-JSON error body is an
        # ORDINARY case, not a fault — uvicorn's own 502/504 pages are HTML.
        # The raw-text branch below is the handler; logging here would fire on
        # every plain-text error and say nothing the caller doesn't already get.
        pass
    try:
        return (resp.text or "").strip()[:400] or "no response body"
    except Exception:  # noqa: BLE001  # silent-ok: this function exists to
        # DESCRIBE a failure the caller is already reporting. Raising or
        # logging here would replace a diagnosis with a second, less useful
        # error; the sentinel keeps the status code (which we still have)
        # reaching the operator.
        return "unreadable response body"


logger = get_logger(__name__)


class ImageModel(str, Enum):
    """Deprecated: the type of the ignored ``model=`` parameter, nothing more.

    The image-gen server (``scripts/image-gen-server.py``) renders the model
    that ``app_settings.image_generation_model`` names, one of the server's own
    ``REGISTRY`` keys, and no request can choose another. These members named
    what the worker's in-process diffusers path could load. That path, and the
    registry, default resolver and ``image_model`` setting that described it,
    were removed on 2026-09-28. A member here says nothing about what the server
    can render: ``flux_schnell`` is not in its REGISTRY, and ``sdxl_turbo`` is
    but has no member.

    Kept unchanged so a caller passing ``model=ImageModel.X`` still imports and
    runs. ``generate_image_result`` logs a WARNING and renders the configured
    model. The parameter and this enum stay for at least one minor release
    after the first one that ships that warning.
    """

    SDXL_BASE = "sdxl_base"
    SDXL_LIGHTNING = "sdxl_lightning"
    FLUX_SCHNELL = "flux_schnell"
    Z_IMAGE_TURBO = "z_image_turbo"


@dataclass(frozen=True)
class ImageGenOutcome:
    """Why a generate attempt ended the way it did (poindexter#1005).

    ``generate_image`` returns a bare ``bool``, so every reason a render
    failed was flattened to ``False`` and only ever reached the worker log.
    The operator surfaces then reported the *symptom* — "image generation
    produced no output" as a 503 — for a failure the image-gen server had
    already diagnosed precisely in its own 503 body ("CUDA out of memory.
    Tried to allocate 76.00 MiB…"), which is the difference between an
    operator who knows to retry and one who has nothing to act on.

    ``reason`` is a stable machine token for branching / findings;
    ``detail`` is the human string to surface (already truncated). Tokens:

    * ``gpu_busy`` — admission refused, or the lock wait timed out.
    * ``server_error`` — image-gen answered non-200 (OOM lives here), or
      could not be reached at all (connection refused, timeout).
    * ``bad_response`` — 200 with an unusable body: unparseable JSON, no
      filename, an unexpected content type, or the rendered image could not
      be fetched.
    * ``write_failed`` — the server rendered the image but the worker could
      not write it to ``output_path``.

    The image-gen server is the only render path, so every failure is one of
    these. The worker's in-process diffusers fallback was removed in 2026-09,
    and with it the ``unavailable`` and ``render_failed`` tokens it produced.
    """

    ok: bool
    reason: str | None = None
    detail: str | None = None

    # Deliberately NOT given a __bool__. Making a failed outcome falsy reads
    # nicely at a call site (`if outcome:`) and is a trap everywhere else — it
    # silently breaks `outcome or fallback`, discarding the diagnosis a failed
    # outcome carries. Callers test ``.ok``.

    @property
    def message(self) -> str:
        """One operator-facing line, safe to put in an HTTP error body."""
        if self.ok:
            return "image generated"
        if self.detail:
            return f"image generation failed ({self.reason}): {self.detail}"
        return f"image generation failed ({self.reason or 'unknown'})"


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


class ImageService:
    """
    Unified service for all image operations.

    Consolidates:
    - PexelsClient functionality (featured image, gallery)
    - ImageGenClient functionality (image-gen generation)
    - ImageAgent functionality (orchestration, metadata)

    All operations are async-first to prevent blocking in FastAPI event loop.
    """

    def __init__(self, site_config: SiteConfig):
        """Initialize image service.

        The Pexels API key is a secret (encrypted in app_settings) and
        therefore NOT loaded into site_config's in-memory cache at
        startup. We defer the actual fetch to ``_ensure_pexels_key()``,
        which runs on first use from an async context via
        ``SiteConfig.get_secret()``.

        Args:
            site_config: Injected ``SiteConfig`` instance (required — #272
                Phase-2e). Resolves the Pexels API key (a secret) and other
                image-pipeline settings. Callers thread the run-bound
                instance via ``get_image_service(site_config=...)`` —
                ``app.state.container.site_config`` (FastAPI),
                ``context["site_config"]`` (pipeline stages), or
                ``config["_site_config"]`` (jobs / image-provider plugins).
        """
        self._site_config = site_config

        self.pexels_api_key: str | None = None
        self._pexels_key_checked_db = False
        self.pexels_available = False
        # #198: tunable for API version changes / private image proxies
        self.pexels_base_url = self._site_config.get(
            "pexels_api_base", "https://api.pexels.com/v1"
        ).rstrip("/")
        self.pexels_headers: dict[str, str] = {"Authorization": self.pexels_api_key} if self.pexels_api_key else {}

        # No image-generation state lives here: the image-gen HTTP server owns
        # the model (see _generate_image_impl), so there is nothing to lazily
        # load, track or unload in the worker.

        self.search_cache: dict[str, list[FeaturedImageMetadata]] = {}

    # =========================================================================
    # DB-FIRST KEY LOADING
    # =========================================================================

    async def _ensure_pexels_key(self) -> None:
        """Load + decrypt the Pexels API key from app_settings.

        Cached per service instance — subsequent calls are no-ops once
        the key is resolved. Routes through ``SiteConfig.get_secret``
        (the canonical Phase H DI seam — see CLAUDE.md "Configuration"
        section) so any caller with a properly-wired ``SiteConfig``
        gets the key without needing the legacy DI-container "database"
        registration that was missed during the Phase H cutover
        (poindexter#381).

        Raises ``RuntimeError`` if the SiteConfig has no DB pool — that
        means the lifespan hasn't run, and we cannot tell the
        difference between "key intentionally unset" and "lookup
        broken". Per ``feedback_no_silent_defaults``, surfacing the
        gap is required.
        """
        if self._pexels_key_checked_db:
            return

        # The SiteConfig MUST have a DB pool to fetch secrets — without
        # it, `get_secret` silently returns the env-var fallback (or
        # default ""), masking config-loading bugs. Refuse to declare
        # pexels unavailable in that state — surface the gap loudly.
        if getattr(self._site_config, "_pool", None) is None:
            raise RuntimeError(
                "ImageService cannot resolve pexels_api_key: SiteConfig "
                "has no DB pool. Wire site_config from app.state (FastAPI) "
                "or context['site_config'] (pipeline stages) into "
                "ImageService(site_config=...) — see CLAUDE.md "
                "Configuration / poindexter#381."
            )

        try:
            value = await self._site_config.get_secret("pexels_api_key", "")
        except Exception as exc:
            # Loud failure: a DB error mid-lookup is a real problem; do
            # not silently mark pexels unavailable.
            self._pexels_key_checked_db = True
            raise RuntimeError(
                f"pexels_api_key lookup failed: {exc}. Refusing to silently "
                "fall back to pexels_available=False (feedback_no_silent_defaults)."
            ) from exc

        self._pexels_key_checked_db = True
        if value:
            self.pexels_api_key = value
            self.pexels_available = True
            self.pexels_headers = {"Authorization": value}
            logger.info("Pexels API key loaded from app_settings (encrypted)")
        else:
            # Empty key is a legitimate state — Pexels is a fallback
            # image source, image-gen is primary. Log info, leave unavailable.
            logger.info(
                "pexels_api_key not set in app_settings — Pexels search "
                "disabled (image-gen remains primary)"
            )

    # =========================================================================
    # FEATURED IMAGE SEARCH (Pexels - Free, Unlimited)
    # =========================================================================

    async def _llm_semantic_pexels_query(self, topic: str) -> str | None:
        """Ask the LLM for a Pexels-friendly semantic query.

        Thin delegator (poindexter#109) — the implementation moved to
        ``services.image_providers.pexels.build_semantic_pexels_query``. It
        only ever touched ``self._site_config``, so it was a pure function
        of ``(topic, site_config)`` masquerading as a method. Kept as a
        method here for existing callers/tests.
        """
        from poindexter.services.image_providers.pexels import build_semantic_pexels_query

        return await build_semantic_pexels_query(topic, site_config=self._site_config)

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
            page: Results page number for pagination (default 1, use higher for different results)

        Returns:
            FeaturedImageMetadata or None if no image found
        """
        import random

        await self._ensure_pexels_key()

        if not self.pexels_api_key:
            logger.warning("Pexels API key not configured (checked env + DB)")
            return None

        # Respect image_aspect_ratio setting for niche-appropriate orientation.
        # Only overrides the default "landscape"; explicit caller-supplied values
        # are left alone so per-call overrides remain authoritative.
        _cfg_aspect = self._site_config.get("image_aspect_ratio", "")
        if _cfg_aspect and orientation == "landscape":
            orientation = _cfg_aspect

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

        # Niche-specific fallback keywords: operator-configurable via
        # image_pexels_fallback_keywords (comma-separated).  When unset the
        # default list is used; it's intentionally generic so it doesn't skew
        # non-tech niches the way the original tech-heavy list did (#219).
        _fallback_kw_setting = self._site_config.get("image_pexels_fallback_keywords", "")
        if _fallback_kw_setting:
            concept_keywords = [
                kw.strip() for kw in _fallback_kw_setting.split(",") if kw.strip()
            ]
        else:
            concept_keywords = [
                "abstract",
                "modern",
                "background",
                "workspace",
                "object",
                "product",
                "design",
                "pattern",
                "texture",
                "nature",
                "landscape",
                "environment",
            ]

        # Add user keywords but avoid person/people related terms
        if keywords:
            for kw in keywords[:3]:
                # Avoid portrait/people searches
                if not any(
                    term in kw.lower() for term in ["person", "people", "portrait", "face", "human"]
                ):
                    search_queries.append(kw)

        # Add combined searches (topic + concept)
        search_queries.append(f"{topic} technology")
        search_queries.append(f"{topic} abstract")
        search_queries.extend(concept_keywords[:2])

        for query in search_queries:
            try:
                logger.info("Searching Pexels for: '%s' (page %s)", query, page)
                images = await self._pexels_search(
                    query, per_page=5, orientation=orientation, size=size, page=page
                )
                if images:
                    # RANDOMIZE IMAGE SELECTION: Pick random image from results instead of always first
                    # This prevents all posts from using the same image when topics are similar
                    metadata = random.choice(images)
                    logger.info(
                        "Found featured image for '%s' using query '%s' (page %s) - randomly selected from %s results",
                        topic, query, page, len(images),
                    )
                    return metadata
            except Exception as e:
                logger.warning("Error searching for '%s': %s", query, e, exc_info=True)

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

        if not self.pexels_api_key:
            logger.warning("Pexels API key not configured (checked env + DB)")
            return []

        search_queries = [topic]
        if keywords:
            search_queries.extend(keywords)

        all_images = []

        for query in search_queries[:3]:  # Try up to 3 queries
            try:
                images = await self._pexels_search(query, per_page=count)
                all_images.extend(images)

                if len(all_images) >= count:
                    logger.info("Found %s gallery images", len(all_images))
                    return all_images[:count]

            except Exception as e:
                logger.warning("Error searching for gallery images '%s': %s", query, e, exc_info=True)

        logger.info("Found %s gallery images (less than requested)", len(all_images))
        return all_images

    async def _pexels_search(
        self,
        query: str,
        per_page: int = 5,
        orientation: str = "landscape",
        size: str = "medium",
        page: int = 1,
    ) -> list[FeaturedImageMetadata]:
        """
        Internal method to search Pexels API (async-only).

        Args:
            query: Search keywords
            per_page: Results per page
            orientation: Image orientation
            size: Image size
            page: Results page number (for pagination)

        Returns:
            List of FeaturedImageMetadata objects
        """
        # Skip search if API key is not configured
        if not self.pexels_api_key:
            logger.debug("Pexels API key not configured - skipping search for '%s'", query)
            return []

        try:
            params = {
                "query": query,
                "per_page": min(per_page, 80),
                "orientation": orientation,
                "size": size,
                "page": page,
            }

            if http_client is not None:
                response = await http_client.get(
                    f"{self.pexels_base_url}/search",
                    headers=self.pexels_headers,
                    params=params,  # type: ignore[arg-type]
                    timeout=10.0,
                )
            else:
                async with httpx.AsyncClient(timeout=10.0) as client:
                    response = await client.get(
                        f"{self.pexels_base_url}/search",
                        headers=self.pexels_headers,
                        params=params,  # type: ignore[arg-type]
                    )
            response.raise_for_status()
            data = response.json()

            photos = data.get("photos", [])
            logger.info(
                "Pexels search for '%s' (page %s) returned %s results",
                query, page, len(photos),
            )

            return [
                FeaturedImageMetadata(
                    url=photo["src"]["large"],
                    thumbnail=photo["src"]["small"],
                    photographer=photo.get("photographer", "Unknown"),
                    photographer_url=photo.get("photographer_url", ""),
                    width=photo.get("width"),
                    height=photo.get("height"),
                    alt_text=photo.get("alt", ""),
                    search_query=query,
                    source="pexels",
                )
                for photo in photos
            ]

        except Exception as e:
            logger.error("Pexels search error: %s", e, exc_info=True)
            return []

    # =========================================================================
    # IMAGE GENERATION (image-gen HTTP server)
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
        """Generate an image. ``True`` on success — the long-standing contract.

        Thin wrapper over :meth:`generate_image_result`; call that instead when
        you need to tell the operator WHY a render failed. ``model`` is
        deprecated and ignored (see :class:`ImageModel`).
        """
        outcome = await self.generate_image_result(
            prompt,
            output_path,
            negative_prompt=negative_prompt,
            num_inference_steps=num_inference_steps,
            guidance_scale=guidance_scale,
            task_id=task_id,
            model=model,
        )
        return outcome.ok

    async def generate_image_result(
        self,
        prompt: str,
        output_path: str,
        negative_prompt: str | None = None,
        num_inference_steps: int | None = None,
        guidance_scale: float | None = None,
        task_id: str | None = None,
        model: ImageModel | None = None,
    ) -> ImageGenOutcome:
        """Generate an image under the GPU lock, reporting why on failure.

        **VRAM guard (poindexter#1005).** This method is the only render path
        that operator surfaces reach — ``poindexter tasks regen-image`` /
        ``add-image`` and ``POST /api/tasks/{id}/generate-image`` — and until
        now it POSTed straight at the image-gen server with no GPU
        coordination at all, so it raced whatever happened to be resident.
        The pipeline never had that problem: ``content.plan_image_markers``
        evicts the writer via ``maybe_unload_writer_before_image_gen`` and
        every pipeline render then runs inside ``gpu.lock("image_gen")``,
        whose acquire path evicts Ollama on EVERY configured host and
        *confirms* the release before yielding. An operator regen issued while
        the ~19 GB writer sat warm therefore OOM'd on a card with 31 GB free
        on paper — observed as three attempts for one featured image, the
        third landing only because the GPU happened to be quiet.

        Taking the same lock fixes it at the root and is strictly better than
        calling the unload helper directly here: it evicts (all hosts,
        confirmed), it serialises against concurrent pipeline renders rather
        than merely dodging Ollama, and it feeds ``gpu_task_sessions`` /
        ``gpu_lease_stats`` like every other GPU consumer. When the caller
        already holds the lock the acquire is a reentrant no-op, so the
        ImageGenProvider plugin path is unaffected.

        ``priority="operator"`` and :func:`operator_image_wait_budget_s` place
        this between pipeline work and background jobs (poindexter#914 P2
        group 3): a human is holding an HTTP request open, so it outranks
        background work but must never displace the pipeline, and a wait it
        cannot survive is refused up front — see that helper for the sizing.

        Bracketed by a best-effort ``kind='media'`` live_activity row so the
        render (a ~5-70s GPU burn, cold model load included) shows in the
        console SYSTEM PULSE instead of being invisible. Liveness-only — a
        single blocking render exposes no mid-progress, so we never fabricate
        a pct (``feedback_no_dummy_data``).

        ``model`` is deprecated and ignored. The server renders
        ``app_settings.image_generation_model`` and takes no per-request model,
        so a caller that sets it gets a WARNING here, before the GPU wait,
        rather than a silently different model.
        """
        if model is not None:
            logger.warning(
                "ImageService: model=%s is deprecated and ignored; the image-gen "
                "server renders app_settings.image_generation_model and takes no "
                "per-request model. Drop the argument: it goes in a later release.",
                getattr(model, "value", model),
            )
        from poindexter.services.gpu_admission import GpuBusyError
        from poindexter.services.gpu_scheduler import (
            GpuLockTimeoutError,
            gpu,
            operator_image_wait_budget_s,
        )

        pool = getattr(self._site_config, "_pool", None)
        _prompt = (prompt or "").strip()
        gpu_label = self._site_config.get("image_generation_model", "image_gen")
        async with live_activity.track(
            pool,
            kind="media",
            ref_id=str(task_id) if task_id else None,
            title=f"Image · {_prompt[:60]}" if _prompt else "Image",
            detail={"medium": "image", "provider": "image_gen"},
            heartbeat_seconds=live_activity.resolve_heartbeat_seconds(self._site_config),
        ) as act:
            try:
                # Reported honestly: on a contended GPU this is where the wall
                # time goes, and "generating" during a 2-minute queue wait
                # would be a lie the SYSTEM PULSE panel repeats to the operator.
                await act.update(step="waiting for gpu")
                async with gpu.lock(
                    "image_gen",
                    model=gpu_label,
                    task_id=task_id,
                    phase="operator_image",
                    max_wait_s=operator_image_wait_budget_s(),
                    priority="operator",
                ):
                    await act.update(step="generating")
                    outcome = await self._generate_image_impl(
                        prompt,
                        output_path,
                        negative_prompt=negative_prompt,
                        num_inference_steps=num_inference_steps,
                        guidance_scale=guidance_scale,
                        task_id=task_id,
                    )
            except (GpuBusyError, GpuLockTimeoutError) as exc:
                # Capacity, not breakage. Returned rather than raised so the
                # bool contract of generate_image() is preserved, and named so
                # the operator surface can say "retry in ~4 min" instead of
                # blaming the renderer for a queue it never reached.
                logger.warning(
                    "image generation skipped — GPU unavailable within budget: %s", exc,
                )
                act.fail()
                return ImageGenOutcome(False, "gpu_busy", str(exc))
            if not outcome.ok:
                act.fail()
            return outcome

    async def _generate_image_impl(
        self,
        prompt: str,
        output_path: str,
        negative_prompt: str | None = None,
        num_inference_steps: int | None = None,
        guidance_scale: float | None = None,
        task_id: str | None = None,
    ) -> ImageGenOutcome:
        """
        Render one image on the image-gen HTTP server and write it to ``output_path``.

        Caller holds ``gpu.lock("image_gen")`` — see
        :meth:`generate_image_result`. This method does the rendering only.

        The server (``scripts/image-gen-server.py``, a GPU-resident container
        on the shared compose network) is the only render path. The worker
        image installs no diffusers, so the in-process fallback that used to
        follow a failed request here could never run. A failure is therefore
        the server's failure, and the outcome carries the server's own
        diagnosis.

        Args:
            prompt: Image generation prompt
            output_path: Local path to save generated image
            negative_prompt: Negative prompt for quality improvement
            num_inference_steps: Override inference steps. Left out of the
                request when None, so the server's per-model registry decides.
                The distilled models ignore an override: the server renders
                z_image_turbo at 9 steps and Lightning at 4 whatever is sent.
            guidance_scale: Override guidance scale. Left out when None, as
                above, and pinned to 0 for those same models.
            task_id: Sent to the server, which stamps it on its
                ``image_ocr_gate_result`` audit row — the same field the
                pipeline's own render paths send.

        The model is not a parameter: the server renders
        ``app_settings.image_generation_model``. The public entry points accept
        a deprecated ``model=`` and warn; it never reaches this method.

        Returns:
            An :class:`ImageGenOutcome` — ``ok`` plus, on failure, the reason
            token and the underlying detail (the image-gen server's own error
            body, where it gave one).
        """
        _sc = self._site_config
        # Addressed by its compose service DNS name so the request never
        # traverses the flaky host-published-port proxy.
        image_gen_server_url = _sc.get("image_gen_server_url", "http://image-gen-server:9836")
        from poindexter.services.settings_defaults import default_int

        render_timeout = _sc.get_int(
            "image_render_timeout_seconds",
            default_int("image_render_timeout_seconds"),
        )
        # Only forward steps / guidance_scale when the caller set them
        # explicitly; otherwise let the image-gen server's per-model registry
        # drive them. The old `or 4` / `or 1.0` fallback forced Stable
        # Diffusion XL-Turbo's params onto z_image_turbo, which is
        # guidance-distilled (wants 9 steps / CFG 0) — the mismatch produced
        # degraded images. Matches replace_inline_images.
        # #image-zimage-and-variety.
        _gen_body: dict[str, object] = {
            "prompt": prompt,
            "negative_prompt": negative_prompt or "",
        }
        if num_inference_steps is not None:
            _gen_body["steps"] = num_inference_steps
        if guidance_scale is not None:
            _gen_body["guidance_scale"] = guidance_scale
        if task_id:
            _gen_body["task_id"] = str(task_id)

        try:
            # Always use a fresh client for image-gen calls. The shared
            # http_client pools keep-alive connections, but uvicorn's
            # default 5s keep-alive means the image-gen server closes the
            # connection between infrequent regen calls. The pooled
            # connection then goes stale and the next request gets
            # "Server disconnected without sending a response".
            # A per-call client never reuses stale connections.
            async with httpx.AsyncClient(
                timeout=httpx.Timeout(float(render_timeout), connect=5.0),
            ) as client:
                resp = await client.post(
                    f"{image_gen_server_url}/generate",
                    json=_gen_body,
                    timeout=render_timeout,
                )
                if resp.status_code != 200:
                    # Carry the server's OWN diagnosis forward. Its 503 body is
                    # the only place the real cause is stated in words — "CUDA
                    # out of memory. Tried to allocate 76.00 MiB", "image-gen
                    # server degraded: <reason>" — and dropping it is what left
                    # the operator with an unactionable "produced no output".
                    detail = _server_error_detail(resp)
                    logger.warning(
                        "image-gen server returned %s: %s", resp.status_code, detail,
                    )
                    return ImageGenOutcome(
                        False, "server_error",
                        f"image-gen server returned HTTP {resp.status_code}: {detail}",
                    )
                ctype = resp.headers.get("content-type", "")
                if ctype.startswith("application/json"):
                    # The sidecar at scripts/image-gen-server.py returns JSON
                    # (image_path + filename + generation_time_ms), NOT raw
                    # bytes. Fetch the actual image via the secondary endpoint
                    # ``GET /images/{filename}`` since the worker container
                    # doesn't share the sidecar's volume mount. Original code
                    # assumed Content-Type: image/* and broke against the JSON
                    # response — see Glad-Labs/glad-labs-stack#334.
                    try:
                        body = resp.json()
                    except ValueError:
                        logger.warning(
                            "image-gen server returned 200 with unparseable JSON: %s",
                            _server_error_detail(resp),
                        )
                        return ImageGenOutcome(
                            False, "bad_response",
                            "image-gen server returned 200 with an unparseable JSON body",
                        )
                    filename = body.get("filename") if isinstance(body, dict) else None
                    if not filename:
                        logger.warning(
                            "image-gen server response missing filename: %s", body,
                        )
                        return ImageGenOutcome(
                            False, "bad_response",
                            "image-gen server returned 200 with no filename",
                        )
                    img_resp = await client.get(
                        f"{image_gen_server_url}/images/{filename}",
                        timeout=render_timeout,
                    )
                    if img_resp.status_code != 200:
                        logger.warning(
                            "image-gen /images/%s returned %s",
                            filename, img_resp.status_code,
                        )
                        return ImageGenOutcome(
                            False, "bad_response",
                            f"fetching the rendered image returned HTTP "
                            f"{img_resp.status_code}",
                        )
                    image_bytes = img_resp.content
                    render_time = f"{body.get('generation_time_ms', '?')}ms"
                elif ctype.startswith("image/"):
                    # Legacy path — sidecar streamed image bytes directly.
                    # Kept for back-compat in case a future sidecar version
                    # reverts to the pre-JSON response shape.
                    image_bytes = resp.content
                    render_time = f"{resp.headers.get('X-Elapsed-Seconds', '?')}s"
                else:
                    logger.warning(
                        "image-gen server returned 200 with content-type %r: %s",
                        ctype, _server_error_detail(resp),
                    )
                    return ImageGenOutcome(
                        False, "bad_response",
                        f"image-gen server returned 200 with content-type "
                        f"{ctype or 'none'}, expected JSON or an image",
                    )
        except Exception as e:
            logger.warning("image-gen host server unavailable (%s)", describe_exception(e))
            # Exception TYPE only, never str(e). `detail` is destined for an
            # HTTP response body, and an httpx ConnectError embeds the resolved
            # address of the host it failed to reach — the disclosure
            # scripts/ci/lint_http_detail_leak.py exists to prevent. The type
            # is the part the operator acts on (ConnectError = container down,
            # ReadTimeout = alive but wedged); the full error is in the log
            # line directly above.
            return ImageGenOutcome(
                False, "server_error",
                f"image-gen server unreachable ({type(e).__name__})",
            )

        try:
            await asyncio.to_thread(_write_image_bytes, output_path, image_bytes)
        except OSError as e:
            # The render succeeded, so this is not the server's failure:
            # reporting it as one would send the operator to a healthy
            # container. Type only in `detail`, for the same reason as above —
            # the message carries the local path.
            logger.error(
                "image-gen rendered the image but writing %s failed: %s",
                output_path, describe_exception(e),
            )
            return ImageGenOutcome(
                False, "write_failed",
                f"the image rendered but could not be written ({type(e).__name__})",
            )
        logger.info(
            "image-gen image generated via host server in %s: %s", render_time, output_path,
        )
        return ImageGenOutcome(True)

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

    def get_search_cache(self, query: str) -> list[FeaturedImageMetadata] | None:
        """Get cached search results"""
        return self.search_cache.get(query)

    def set_search_cache(self, query: str, results: list[FeaturedImageMetadata]) -> None:
        """Cache search results (24-hour TTL in production)"""
        self.search_cache[query] = results


def get_image_service(site_config: SiteConfig) -> ImageService:
    """Factory function for dependency injection.

    Args:
        site_config: Injected ``SiteConfig`` instance (required — #272
            Phase-2e). Forwarded to ``ImageService.__init__``. Pipeline
            stages pull this from ``context['site_config']``; jobs /
            image-provider plugins from ``config['_site_config']``; routes
            from ``app.state.container.site_config``.
    """
    return ImageService(site_config=site_config)
