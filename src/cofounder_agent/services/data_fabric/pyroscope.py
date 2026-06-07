"""Thin async read-only client for Pyroscope HTTP API."""

from __future__ import annotations

import httpx

from .errors import DataFabricError

DEFAULT_URL = "http://localhost:4040"


class PyroscopeClient:
    """Read-only async helper for Pyroscope /pyroscope endpoints."""

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
                "data_fabric_pyroscope_url", DEFAULT_URL
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
                "pyroscope",
                f"HTTP {response.status_code}: {response.text[:200]}",
                status_code=response.status_code,
            )
        return response.json()

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    async def profile_types(self) -> list[str]:
        """Return all available profile type IDs."""
        data = await self._get("/pyroscope/profile-types", {})
        return [pt.get("id", "") for pt in data.get("profileTypes", [])]

    async def label_names(self) -> list[str]:
        """Return all label names."""
        data = await self._get("/pyroscope/label-names", {})
        return data.get("names", [])

    async def label_values(self, label: str) -> list[str]:
        """Return all values for a given label name."""
        data = await self._get("/pyroscope/label-values", {"labelName": label})
        return data.get("values", [])

    async def query(
        self,
        profile_type: str,
        label_selector: str,
        start: str,
        end: str,
    ) -> dict:
        """Query a profile and return the raw flamegraph data."""
        params = {
            "profileTypeID": profile_type,
            "labelSelector": label_selector,
            "start": start,
            "end": end,
        }
        return await self._get("/pyroscope/render", params)

    async def aclose(self) -> None:
        if self._owned:
            await self._client.aclose()
