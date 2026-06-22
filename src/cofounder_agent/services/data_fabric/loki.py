"""Thin async read-only client for Loki HTTP API."""

from __future__ import annotations

import httpx

from .errors import DataFabricError

DEFAULT_URL = "http://loki:3100"


class LokiClient:
    """Read-only async helper for Loki /loki/api/v1 endpoints."""

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
                "data_fabric_loki_url", DEFAULT_URL
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
                "loki",
                f"HTTP {response.status_code}: {response.text[:200]}",
                status_code=response.status_code,
            )
        return response.json()

    def _check_response(self, data: dict) -> None:
        if data.get("status") == "error":
            raise DataFabricError(
                "loki",
                data.get("error", "unknown error"),
            )

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    async def query(self, logql: str, limit: int = 100) -> list[dict]:
        """Instant LogQL query.  Returns the streams list."""
        params = {"query": logql, "limit": str(limit)}
        data = await self._get("/loki/api/v1/query", params)
        self._check_response(data)
        result = data.get("data", {})
        return result.get("result", [])

    async def query_range(
        self,
        logql: str,
        start: str,
        end: str,
        limit: int = 100,
        direction: str = "backward",
    ) -> list[dict]:
        """Range LogQL query.  Returns the streams list."""
        params = {
            "query": logql,
            "start": start,
            "end": end,
            "limit": str(limit),
            "direction": direction,
        }
        data = await self._get("/loki/api/v1/query_range", params)
        self._check_response(data)
        result = data.get("data", {})
        return result.get("result", [])

    async def labels(self) -> list[str]:
        """Return all label names."""
        data = await self._get("/loki/api/v1/labels", {})
        self._check_response(data)
        return data.get("data", [])

    async def label_values(self, label: str) -> list[str]:
        """Return all values for a given label name."""
        data = await self._get(f"/loki/api/v1/label/{label}/values", {})
        self._check_response(data)
        return data.get("data", [])

    async def aclose(self) -> None:
        if self._owned:
            await self._client.aclose()
