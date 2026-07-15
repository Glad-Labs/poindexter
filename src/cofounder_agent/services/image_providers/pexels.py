"""PexelsProvider — search the free Pexels stock-photo API.

The Pexels API (https://www.pexels.com/api/) is free, no rate-limit
gotchas at our scale, and covers most of what the pipeline needs for
inline content images. image-gen handles the "must be on-brand" featured
images; Pexels handles the "just need a relevant photo" inline cases.

Config (``plugin.image_provider.pexels`` in app_settings):

- ``enabled`` (default true)
- ``config.api_key`` — required; if empty, ``fetch()`` returns ``[]``.
  Normally NOT set in config directly — the provider reads it from
  ``app_settings.pexels_api_key`` (is_secret=true, pgcrypto-encrypted)
  the same way image_service.py does. Explicit config override wins
  so tests can inject a fake key without DB round-trips.
- ``config.per_page`` (default 5)
- ``config.orientation`` (default ``"landscape"``)
- ``config.size`` (default ``"medium"``)
- ``config.page`` (default 1) — for pagination

Kind: ``"search"`` — matches external catalog against the query term.
"""

from __future__ import annotations

import logging
from typing import Any

import httpx

from plugins.image_provider import ImageResult
from services.site_config import SiteConfig

logger = logging.getLogger(__name__)


_PEXELS_API_BASE = "https://api.pexels.com/v1"


# Lifespan-bound shared httpx.AsyncClient — main.py wires this via
# set_http_client() at startup. ``fetch`` prefers it so the Pexels
# TLS session is reused across per-task inline-image searches.
http_client: httpx.AsyncClient | None = None


def set_http_client(client: httpx.AsyncClient | None) -> None:
    """Wire the lifespan-bound shared httpx.AsyncClient."""
    global http_client
    http_client = client


class PexelsProvider:
    """Search Pexels for stock photos matching a free-text query."""

    name = "pexels"
    kind = "search"

    async def fetch(
        self,
        query_or_prompt: str,
        config: dict[str, Any],
    ) -> list[ImageResult]:
        api_key = str(config.get("api_key", "") or "")
        if not api_key:
            # Fall back to the shared encrypted setting. Kept as a
            # secondary path so operators who haven't set a per-provider
            # override still get a working provider.
            api_key = await _load_pexels_api_key_from_settings()

        if not api_key:
            logger.debug(
                "PexelsProvider: no api_key configured, skipping query %r",
                query_or_prompt,
            )
            return []

        if not query_or_prompt.strip():
            return []

        per_page = int(config.get("per_page", 5) or 5)
        orientation = str(config.get("orientation", "landscape") or "landscape")
        size = str(config.get("size", "medium") or "medium")
        page = int(config.get("page", 1) or 1)

        params = {
            "query": query_or_prompt,
            "per_page": min(per_page, 80),  # Pexels caps at 80 per page
            "orientation": orientation,
            "size": size,
            "page": page,
        }
        headers = {"Authorization": api_key}

        if http_client is not None:
            resp = await http_client.get(
                f"{_PEXELS_API_BASE}/search",
                headers=headers,
                params=params,  # type: ignore[arg-type]
                timeout=10.0,
            )
        else:
            async with httpx.AsyncClient(timeout=10.0) as client:
                resp = await client.get(
                    f"{_PEXELS_API_BASE}/search",
                    headers=headers,
                    params=params,  # type: ignore[arg-type]
                )
        resp.raise_for_status()
        data = resp.json()

        photos = data.get("photos", []) if isinstance(data, dict) else []
        logger.info(
            "PexelsProvider: query=%r page=%d returned %d results",
            query_or_prompt, page, len(photos),
        )

        results: list[ImageResult] = []
        for photo in photos:
            if not isinstance(photo, dict):
                continue
            src = photo.get("src", {})
            if not isinstance(src, dict):
                continue
            url = src.get("large") or src.get("medium") or src.get("original")
            if not url:
                continue
            results.append(
                ImageResult(
                    url=url,
                    thumbnail=src.get("small", "") or url,
                    photographer=photo.get("photographer", "Unknown"),
                    photographer_url=photo.get("photographer_url", ""),
                    width=photo.get("width"),
                    height=photo.get("height"),
                    alt_text=photo.get("alt", ""),
                    source=self.name,
                    search_query=query_or_prompt,
                    metadata={"pexels_id": photo.get("id")},
                )
            )
        return results


async def build_semantic_pexels_query(topic: str, *, site_config: SiteConfig) -> str | None:
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

    Moved out of ``services.image_service.ImageService._llm_semantic_pexels_query``
    (poindexter#109) — this touched only ``self._site_config``, so it was a
    pure function of ``(topic, site_config)`` masquerading as a method. The
    ``ImageService`` method is now a thin delegator to this function.
    """
    # Migrated v2.2 → dispatcher (glad-labs-stack#2199): the query call
    # follows the pipeline's provider config on the ``free`` tier
    # (``plugin.llm_provider.primary.free``) so image_service stays
    # swap-able across backends (Ollama, vllm, llama.cpp) AND a cloud
    # ``image_search_query_model`` reaches LiteLLM instead of 404ing
    # against local Ollama.
    #
    # 2026-05-12 fix (still holds): ``asyncio`` is stdlib and must always
    # resolve; the first-party provider imports must too. Swallowing
    # ImportError as a "fall back to raw topic" path hid setup-time bugs as
    # runtime degradation. So the imports below are NOT wrapped — an
    # ImportError propagates and fails loud rather than silently degrading
    # featured-image quality. (``get_all_llm_providers`` is imported inside
    # the no-pool branch because only that fallback needs it.)
    import asyncio

    # Tuning constants via app_settings (#198).
    _sc = site_config
    _client_timeout = _sc.get_int("image_ollama_client_timeout_seconds", 30)
    # Per-step model pin (image_search_query_model). Empty → page (advisory)
    # and return None so the caller falls back to the raw topic.
    _model = (_sc.get("image_search_query_model") or "").removeprefix("ollama/")
    if not _model:
        from services.integrations.operator_notify import notify_operator
        await notify_operator(
            "image_service: image_search_query_model is empty — semantic "
            "Pexels query skipped (falling back to raw topic)",
            critical=False,
            site_config=_sc,
        )
        return None
    _max_tokens = _sc.get_int("image_search_query_max_tokens", 30)
    _temp = _sc.get_float("image_search_query_temperature", 0.4)
    _generate_timeout = _sc.get_int("image_search_query_timeout_seconds", 20)

    # ImageService already reaches the DB pool via the SiteConfig it was
    # constructed with (see ``_ensure_pexels_key``'s ``_pool`` guard). With
    # a pool we defer provider selection to ``dispatch_complete``; the local
    # ``ollama_native`` provider is resolved ONLY for the no-pool fallback
    # (tests / bootstrap).
    _pool = getattr(_sc, "_pool", None)
    local_provider = None
    if _pool is None:
        from plugins.registry import get_all_llm_providers
        providers = {p.name: p for p in get_all_llm_providers()}
        local_provider = providers.get("ollama_native")
        if local_provider is None:
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
        "Respond with ONLY the search query (3-5 words, no quotes, no explanation):"
    )

    messages = [{"role": "user", "content": prompt}]
    try:
        if _pool is not None:
            # Production path — the SAME dispatcher the pipeline's LLM calls
            # use. A cloud model routes to LiteLLM (cost-tracked +
            # cost_guard-gated); a local one stays local + free. The old
            # hardcoded ollama_native call POSTed a cloud model name to
            # local Ollama and 404'd (the #2199 class).
            from services.llm_providers.dispatcher import dispatch_complete
            completion = await asyncio.wait_for(
                dispatch_complete(
                    _pool,
                    messages,
                    _model,
                    tier="free",
                    phase="image_search_query",
                    temperature=_temp,
                    max_tokens=_max_tokens,
                    timeout_s=_client_timeout,
                ),
                timeout=_generate_timeout,
            )
        else:
            # No pool (tests / bootstrap): call the local Ollama provider
            # directly. ``local_provider`` was resolved above (the pool-is-
            # None branch returns early when ollama_native is missing).
            if local_provider is None:  # pragma: no cover - resolved above
                return None
            completion = await asyncio.wait_for(
                local_provider.complete(
                    messages=messages,
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
        # silent-ok: best-effort query enrichment — the docstring's own
        # contract is "return None, caller falls back to the raw topic",
        # so a failure here is an expected soft-fail, not a bug to surface.
        logger.debug("LLM semantic query failed for '%s': %s", topic[:40], e)
    return None


async def _load_pexels_api_key_from_settings() -> str:
    """Fetch the encrypted ``pexels_api_key`` from app_settings.

    Uses the shared DI container's DatabaseService; falls back to empty
    string when the container isn't populated (e.g. unit tests that
    construct the provider directly without an app lifespan).
    """
    from services.container import get_service
    db = get_service("database")
    if db is None or not getattr(db, "pool", None):
        return ""

    from plugins.secrets import get_secret
    async with db.pool.acquire() as conn:
        value = await get_secret(conn, "pexels_api_key")
    return value or ""
