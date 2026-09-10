"""Thin async read-only client for Tempo HTTP API."""

from __future__ import annotations

import httpx

from .errors import DataFabricError

DEFAULT_URL = "http://tempo:3200"


class TempoClient:
    """Read-only async helper for Tempo /api endpoints."""

    def __init__(
        self,
        *,
        url: str | None = None,
        site_config=None,
        http_client: httpx.AsyncClient | None = None,
    ) -> None:
        if url is not None:
            self._url = url.rstrip("/")
        elif site_config is not None:
            self._url = site_config.get(
                "data_fabric_tempo_url", DEFAULT_URL
            ).rstrip("/")
        else:
            self._url = DEFAULT_URL

        self._client = http_client or httpx.AsyncClient(timeout=30.0)
        self._owned = http_client is None

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    async def _get(self, path: str, params: dict) -> dict:
        url = f"{self._url}{path}"
        response = await self._client.get(url, params=params)
        if response.status_code != 200:
            raise DataFabricError(
                "tempo",
                f"HTTP {response.status_code}: {response.text[:200]}",
                status_code=response.status_code,
            )
        return response.json()

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    async def search(
        self,
        tags: dict | None = None,
        min_duration: str | None = None,
        max_duration: str | None = None,
        limit: int = 20,
        start: str | None = None,
        end: str | None = None,
    ) -> list[dict]:
        """Search traces by tag filters.  Returns the traces list."""
        params: dict = {"limit": str(limit)}
        if tags:
            # Tempo accepts tags as space-separated key=value pairs
            params["tags"] = " ".join(f"{k}={v}" for k, v in tags.items())
        if min_duration is not None:
            params["minDuration"] = min_duration
        if max_duration is not None:
            params["maxDuration"] = max_duration
        if start is not None:
            params["start"] = start
        if end is not None:
            params["end"] = end
        data = await self._get("/api/search", params)
        return data.get("traces", [])

    async def get_trace(self, trace_id: str) -> dict:
        """Fetch a single trace by ID."""
        data = await self._get(f"/api/traces/{trace_id}", {})
        return data

    async def search_tags(self) -> list[str]:
        """Return all searchable tag names."""
        data = await self._get("/api/search/tags", {})
        return data.get("tagNames", [])

    async def aclose(self) -> None:
        if self._owned:
            await self._client.aclose()
