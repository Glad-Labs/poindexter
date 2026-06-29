"""Thin httpx wrapper for the Postiz REST API.

Credentials are passed in at construction time by the caller — never
captured at module import (DB-first config rule, CLAUDE.md).

Postiz self-hosted REST API: POST {base_url}/public/v1/posts
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any

import httpx

logger = logging.getLogger(__name__)

_TIMEOUT = 30.0
_UPLOAD_TIMEOUT = 90.0

# Postiz validates each post's `settings` against the platform's DTO and
# rejects (HTTP 400) any post missing a required field — even on the
# self-hosted public API. These are the per-platform-type required-setting
# defaults, merged UNDER caller-supplied platform_settings so a caller can
# always override them. Extend this map as new platforms are connected
# (each platform's `*.dto` lists its non-IsOptional fields).
#   x: who_can_reply_post is the sole required field (XDto, no @IsOptional).
_PLATFORM_SETTING_DEFAULTS: dict[str, dict[str, Any]] = {
    "x": {"who_can_reply_post": "everyone"},
}


class PostizClient:
    def __init__(self, base_url: str, api_key: str = "") -> None:
        self._base = base_url.rstrip("/")
        self._headers: dict[str, str] = {"Content-Type": "application/json"}
        if api_key:
            self._headers["Authorization"] = api_key

    async def create_post(
        self,
        integration_id: str,
        content: str,
        platform_type: str,
        platform_settings: dict[str, Any],
        upload_ids: list[str],
    ) -> dict[str, Any]:
        """Post to a social platform via Postiz.

        Returns {"success": bool, "post_id": str | None, "error": str | None}.
        Never raises — all errors become failure dicts.
        """
        now_iso = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        platform_defaults = _PLATFORM_SETTING_DEFAULTS.get(platform_type, {})
        settings: dict[str, Any] = {
            "__type": platform_type,
            **platform_defaults,
            **platform_settings,
        }
        images = [{"id": uid} for uid in upload_ids]
        payload = {
            "type": "now",
            "date": now_iso,
            "shortLink": False,
            "tags": [],
            "posts": [
                {
                    "integration": {"id": integration_id},
                    "value": [{"content": content, "image": images}],
                    "settings": settings,
                }
            ],
        }
        try:
            async with httpx.AsyncClient() as http:
                resp = await http.post(
                    f"{self._base}/public/v1/posts",
                    json=payload,
                    headers=self._headers,
                    timeout=_TIMEOUT,
                )
                resp.raise_for_status()
                data = resp.json()
                post_id = str(data.get("id", "")) or None
                return {"success": True, "post_id": post_id, "error": None}
        except httpx.HTTPStatusError as exc:
            err = f"Postiz HTTP {exc.response.status_code}: {exc.response.text[:200]}"
            logger.error("[PostizClient] %s — platform=%s", err, platform_type)
            return {"success": False, "post_id": None, "error": err}
        except Exception as exc:
            err = str(exc)
            logger.error("[PostizClient] create_post failed: %s", err)
            return {"success": False, "post_id": None, "error": err}

    async def upload_from_url(self, video_url: str) -> str:
        """Upload a video from a URL to Postiz and return the upload ID.

        Postiz fetches the URL server-side. Raises on failure (callers
        mark draft failed and alert).
        """
        payload = {"url": video_url}
        async with httpx.AsyncClient() as http:
            resp = await http.post(
                f"{self._base}/public/v1/uploads/url",
                json=payload,
                headers=self._headers,
                timeout=_UPLOAD_TIMEOUT,
            )
            resp.raise_for_status()
            data = resp.json()
            upload_id = str(data.get("id", ""))
            if not upload_id:
                raise ValueError(f"Postiz upload returned no id: {data}")
            return upload_id
