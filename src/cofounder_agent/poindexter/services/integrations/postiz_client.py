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

# Each entry of a post value's `image` array is a Postiz MediaDto: `id` AND
# `path` are both @IsDefined, and `path` must pass ValidUrlPath +
# ValidUrlExtension (checked in the v2.24.0 image, 2026-09-28). An id-only item
# is rejected with a 400, so a media post needs what upload_media_from_url
# returns, not just the upload id.


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
        upload_ids: list[str] | None = None,
        *,
        media: list[dict[str, str]] | None = None,
    ) -> dict[str, Any]:
        """Post to a social platform via Postiz.

        ``media`` is a list of ``{"id", "path"}`` dicts as returned by
        :meth:`upload_media_from_url` — the shape Postiz accepts. ``upload_ids``
        is kept for existing callers; an id without its path fails Postiz's
        MediaDto validation, so pass ``media`` for anything with an attachment.

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
        if media:
            images = [{"id": m["id"], "path": m["path"]} for m in media]
        else:
            images = [{"id": uid} for uid in upload_ids or []]
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
                # Postiz returns a list of {postId, integration} — one entry
                # per post in the group (POST /public/v1/posts). A 200 means
                # Postiz ACCEPTED + queued the post; the actual platform
                # publish runs async (it can still land in ERROR state on the
                # Postiz side, e.g. an X "CreditsDepleted" rejection).
                post_id = _extract_post_id(data)
                return {"success": True, "post_id": post_id, "error": None}
        except httpx.HTTPStatusError as exc:
            err = f"Postiz HTTP {exc.response.status_code}: {exc.response.text[:200]}"
            logger.error("[PostizClient] %s — platform=%s", err, platform_type)
            return {"success": False, "post_id": None, "error": err}
        except Exception as exc:
            err = str(exc)
            logger.error("[PostizClient] create_post failed: %s", err)
            return {"success": False, "post_id": None, "error": err}

    async def list_posts(self, start_iso: str, end_iso: str) -> list[dict[str, Any]]:
        """List Postiz posts whose publishDate falls in [start, end].

        ``GET /public/v1/posts`` — each entry carries ``id``, ``state``
        (``QUEUE`` | ``PUBLISHED`` | ``ERROR`` | ``DRAFT``), ``releaseURL``,
        ``content``, ``publishDate``, and ``integration``. This is the only
        public read surface for delivery state (there is no single-post GET),
        so callers window the query and match by id. Raises on HTTP failure
        (callers surface the error; a delivery-state sync must not silently
        report "all fine" on an unreachable Postiz — fail loud per
        feedback_no_silent_defaults).
        """
        async with httpx.AsyncClient() as http:
            resp = await http.get(
                f"{self._base}/public/v1/posts",
                params={"startDate": start_iso, "endDate": end_iso},
                headers=self._headers,
                timeout=_TIMEOUT,
            )
            resp.raise_for_status()
            data = resp.json()
            posts = data.get("posts", []) if isinstance(data, dict) else data
            return posts if isinstance(posts, list) else []

    async def upload_media_from_url(self, media_url: str) -> dict[str, str]:
        """Have Postiz fetch ``media_url`` into its media library.

        Returns ``{"id", "path"}`` — both are needed to attach the upload to a
        post (see the MediaDto note above). Postiz fetches the URL server-side
        and refuses internal or plain-HTTP URLs, so pass a public HTTPS URL.
        Raises on failure (callers mark the draft failed and alert).

        The route is ``/public/v1/upload-from-url``. This used to call
        ``/public/v1/uploads/url``, which Postiz never served: a live check on
        2026-09-28 got 404 there and 201 here, with a body of ``id``, ``name``,
        ``path``, ``originalName``, ``thumbnail``, ``alt`` and ``status``.
        """
        async with httpx.AsyncClient() as http:
            resp = await http.post(
                f"{self._base}/public/v1/upload-from-url",
                json={"url": media_url},
                headers=self._headers,
                timeout=_UPLOAD_TIMEOUT,
            )
            resp.raise_for_status()
            data = resp.json()
        upload_id = str(data.get("id") or "")
        path = str(data.get("path") or "")
        if not upload_id or not path:
            raise ValueError(f"Postiz upload returned no id/path: {data}")
        return {"id": upload_id, "path": path}

    async def upload_from_url(self, video_url: str) -> str:
        """Upload from a URL and return only the upload id.

        Kept for existing callers. A post attachment needs the path as well,
        so new code should use :meth:`upload_media_from_url`.
        """
        return (await self.upload_media_from_url(video_url))["id"]


def _extract_post_id(data: Any) -> str | None:
    """Pull the Postiz post id out of a create-post response.

    POST /public/v1/posts returns a LIST of ``{postId, integration}`` (one
    per post in the group). Older/other shapes may return a dict with ``id``.
    Returns the first post's id, or None if absent.
    """
    if isinstance(data, list):
        if not data:
            return None
        first = data[0]
        if isinstance(first, dict):
            return str(first.get("postId") or first.get("id") or "") or None
        return None
    if isinstance(data, dict):
        return str(data.get("id", "")) or None
    return None
