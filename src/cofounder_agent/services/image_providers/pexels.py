"""PexelsProvider — search the free Pexels stock-photo API.

The Pexels API (https://www.pexels.com/api/) is free, no rate-limit
gotchas at our scale, and covers most of what the pipeline needs for
inline content images. SDXL handles the "must be on-brand" featured
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
                params=params,
                timeout=10.0,
            )
        else:
            async with httpx.AsyncClient(timeout=10.0) as client:
                resp = await client.get(
                    f"{_PEXELS_API_BASE}/search",
                    headers=headers,
                    params=params,
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
