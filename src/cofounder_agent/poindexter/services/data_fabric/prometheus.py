"""Thin async read-only client for Prometheus HTTP API."""

from __future__ import annotations

import httpx

from .errors import DataFabricError

DEFAULT_URL = "http://prometheus:9090"


class PrometheusClient:
    """Read-only async helper for Prometheus /api/v1 endpoints."""

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
                "data_fabric_prometheus_url", DEFAULT_URL
            ).rstrip("/")
        else:
            self._url = DEFAULT_URL

        self._client = http_client or httpx.AsyncClient(timeout=30.0)
        self._owned = http_client is None

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _check_response(self, data: dict) -> None:
        """Raise DataFabricError if the Prometheus response signals an error."""
        if data.get("status") == "error":
            raise DataFabricError(
                "prometheus",
                data.get("error", "unknown error"),
            )

    async def _get(self, path: str, params: dict) -> dict:
        url = f"{self._url}{path}"
        response = await self._client.get(url, params=params)
        if response.status_code != 200:
            raise DataFabricError(
                "prometheus",
                f"HTTP {response.status_code}: {response.text[:200]}",
                status_code=response.status_code,
            )
        return response.json()

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    async def query(self, promql: str, time: str | None = None) -> list[dict]:
        """Instant query.  Returns data.result list."""
        params: dict = {"query": promql}
        if time is not None:
            params["time"] = time
        data = await self._get("/api/v1/query", params)
        self._check_response(data)
        return data["data"]["result"]

    async def query_range(
        self,
        promql: str,
        start: str,
        end: str,
        step: str = "60s",
    ) -> list[dict]:
        """Range query.  Returns data.result list."""
        params = {"query": promql, "start": start, "end": end, "step": step}
        data = await self._get("/api/v1/query_range", params)
        self._check_response(data)
        return data["data"]["result"]

    async def labels(self) -> list[str]:
        """Return all label names."""
        data = await self._get("/api/v1/labels", {})
        self._check_response(data)
        return data["data"]

    async def aclose(self) -> None:
        if self._owned:
            await self._client.aclose()
