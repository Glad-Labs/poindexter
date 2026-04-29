"""Shared worker-API HTTP client for poindexter CLI subcommands.

Every subcommand group that hits the local FastAPI worker (tasks, posts,
costs, quality, settings) imports `WorkerClient` from this module so they
all share authentication, URL resolution, and error handling logic.

URL resolution order (#198: no silent defaults):
    1. POINDEXTER_API_URL env var
    2. WORKER_API_URL env var (legacy)
    3. raises RuntimeError loudly — no localhost fallback

Auth token resolution order:
    1. POINDEXTER_KEY env var
    2. GLADLABS_KEY env var (backward compat with old openclaw skills)
    3. raises RuntimeError loudly (per "no silent defaults" rule)
"""

from __future__ import annotations

import os
from contextlib import suppress
from typing import Any

import httpx


class WorkerClient:
    """Minimal async httpx wrapper around the Poindexter worker API."""

    def __init__(self, base_url: str | None = None, token: str | None = None) -> None:
        resolved_url = (
            base_url
            or os.getenv("POINDEXTER_API_URL")
            or os.getenv("WORKER_API_URL")
        )
        if not resolved_url:
            raise RuntimeError(
                "No worker API URL configured. Set POINDEXTER_API_URL (preferred) "
                "or WORKER_API_URL in the environment. For local dev this is "
                "typically http://localhost:8002, but there is no hardcoded "
                "default — you must configure it explicitly (#198)."
            )
        self.base_url = resolved_url.rstrip("/")
        self.token = token or os.getenv("POINDEXTER_KEY") or os.getenv("GLADLABS_KEY") or ""
        if not self.token:
            raise RuntimeError(
                "No API token found. Set POINDEXTER_KEY (preferred) or GLADLABS_KEY "
                "in the environment. Get the value from `app_settings.api_token` "
                "via `poindexter settings get api_token` (or the DB directly)."
            )
        self._client: httpx.AsyncClient | None = None

    async def __aenter__(self) -> WorkerClient:
        self._client = httpx.AsyncClient(
            base_url=self.base_url,
            headers={
                "Authorization": f"Bearer {self.token}",
                "Content-Type": "application/json",
            },
            timeout=30.0,
        )
        return self

    async def __aexit__(self, exc_type, exc, tb) -> None:
        if self._client is not None:
            with suppress(Exception):
                # aclose() failure during teardown is non-actionable — no
                # outstanding requests to salvage, and raising here would
                # mask the original exception if __aexit__ is in finally.
                await self._client.aclose()
            self._client = None

    async def get(self, path: str, **kwargs: Any) -> httpx.Response:
        assert self._client is not None
        return await self._client.get(path, **kwargs)

    async def post(self, path: str, **kwargs: Any) -> httpx.Response:
        assert self._client is not None
        return await self._client.post(path, **kwargs)

    async def put(self, path: str, **kwargs: Any) -> httpx.Response:
        assert self._client is not None
        return await self._client.put(path, **kwargs)

    async def json_or_raise(self, resp: httpx.Response) -> Any:
        """Return parsed JSON on 2xx, otherwise raise a click-friendly error."""
        if 200 <= resp.status_code < 300:
            try:
                return resp.json()
            except ValueError:
                return {"raw": resp.text}
        # Non-2xx: include the body so CLI errors are diagnostic, not just "500 Internal Server Error"
        try:
            body = resp.json()
        except ValueError:
            body = resp.text
        raise RuntimeError(
            f"HTTP {resp.status_code} from {resp.request.method} {resp.request.url}: {body}"
        )
