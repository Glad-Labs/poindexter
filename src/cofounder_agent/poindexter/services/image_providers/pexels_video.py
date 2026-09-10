"""PexelsVideoProvider — search Pexels' free stock-VIDEO API.

Sibling of ``pexels.py`` (photo search) — same Pexels account, same
``pexels_api_key`` secret, different endpoint. Exists because the video
director prompt (``skills/content/video-director/SKILL.md``) has always
told the LLM that ``source="pexels"`` shots are "stock video clips" /
"real footage", but ``shot_list_renderer.py`` only ever wired up the
PHOTO search — every "pexels" shot shipped as a static held frame. This
provider closes that gap; ``shot_list_renderer._render_pexels_video``
tries it first and falls back to the existing still-photo path when a
query has no video match.

Config (``plugin.image_provider.pexels_video`` in app_settings):

- ``enabled`` (default true)
- ``config.api_key`` — required; if empty, ``fetch()`` returns ``[]``.
  Reads ``app_settings.pexels_api_key`` (is_secret=true) same as the
  photo provider — one Pexels account covers both photo and video search.
- ``config.per_page`` (default 5)
- ``config.orientation`` (default ``"landscape"``)
- ``config.page`` (default 1) — for pagination

Kind: ``"search"`` — matches external catalog against the query term.
"""

from __future__ import annotations

import logging
from typing import Any

import httpx

from plugins.video_provider import VideoResult

logger = logging.getLogger(__name__)


_PEXELS_VIDEO_API_BASE = "https://api.pexels.com/videos"


# Lifespan-bound shared httpx.AsyncClient — main.py wires this via
# set_http_client() at startup (WIRED_HTTP_CLIENT_MODULES), same as the
# sibling photo provider, so the Pexels TLS session is reused across
# per-shot video searches.
http_client: httpx.AsyncClient | None = None


def set_http_client(client: httpx.AsyncClient | None) -> None:
    """Wire the lifespan-bound shared httpx.AsyncClient."""
    global http_client
    http_client = client

# Preferred quality tiers, best first. Pexels' own catalog: "sd", "hd",
# "uhd". "hd" is the sweet spot for a shot clip (looks good at 1080p
# render targets without a multi-hundred-MB uhd download); fall back to
# whatever's actually available rather than dropping the video.
_PREFERRED_QUALITY_ORDER = ("hd", "sd", "uhd")


def _pick_video_file(video_files: list[dict]) -> dict | None:
    """Pick one ``video_files[]`` entry to download.

    Prefers ``quality="hd"``, then ``"sd"``, then ``"uhd"``; an
    unrecognized quality label still gets picked over dropping the video
    entirely. Only considers ``file_type == "video/mp4"`` entries with a
    non-empty ``link`` — Pexels also lists webm/hls variants we don't
    want. Returns ``None`` when nothing usable is present.
    """
    by_quality: dict[str, list[dict]] = {}
    for f in video_files or []:
        if not isinstance(f, dict):
            continue
        if f.get("file_type") != "video/mp4" or not f.get("link"):
            continue
        by_quality.setdefault(str(f.get("quality") or ""), []).append(f)

    for q in _PREFERRED_QUALITY_ORDER:
        if q in by_quality:
            return by_quality[q][0]
    for files in by_quality.values():
        return files[0]
    return None


class PexelsVideoProvider:
    """Search Pexels for stock video clips matching a free-text query."""

    name = "pexels_video"
    kind = "search"

    async def fetch(
        self,
        query_or_prompt: str,
        config: dict[str, Any],
    ) -> list[VideoResult]:
        api_key = str(config.get("api_key", "") or "")
        if not api_key:
            # Same fallback as the photo provider — one Pexels account,
            # one encrypted setting, both search endpoints.
            from services.image_providers.pexels import (
                _load_pexels_api_key_from_settings,
            )
            api_key = await _load_pexels_api_key_from_settings()

        if not api_key:
            logger.debug(
                "PexelsVideoProvider: no api_key configured, skipping query %r",
                query_or_prompt,
            )
            return []

        if not query_or_prompt.strip():
            return []

        per_page = int(config.get("per_page", 5) or 5)
        orientation = str(config.get("orientation", "landscape") or "landscape")
        page = int(config.get("page", 1) or 1)

        params = {
            "query": query_or_prompt,
            "per_page": min(per_page, 80),  # Pexels caps at 80 per page
            "orientation": orientation,
            "page": page,
        }
        headers = {"Authorization": api_key}

        if http_client is not None:
            resp = await http_client.get(
                f"{_PEXELS_VIDEO_API_BASE}/search",
                headers=headers,
                params=params,  # type: ignore[arg-type]
                timeout=10.0,
            )
        else:
            async with httpx.AsyncClient(timeout=10.0) as client:
                resp = await client.get(
                    f"{_PEXELS_VIDEO_API_BASE}/search",
                    headers=headers,
                    params=params,  # type: ignore[arg-type]
                )
        resp.raise_for_status()
        data = resp.json()

        videos = data.get("videos", []) if isinstance(data, dict) else []
        logger.info(
            "PexelsVideoProvider: query=%r page=%d returned %d results",
            query_or_prompt, page, len(videos),
        )

        results: list[VideoResult] = []
        for video in videos:
            if not isinstance(video, dict):
                continue
            picked = _pick_video_file(video.get("video_files", []))
            if picked is None:
                continue
            user = video.get("user", {})
            results.append(
                VideoResult(
                    file_url=str(picked["link"]),
                    file_path=None,
                    duration_s=int(video.get("duration") or 0),
                    width=picked.get("width"),
                    height=picked.get("height"),
                    codec="h264",
                    format="mp4",
                    source=self.name,
                    prompt=query_or_prompt,
                    metadata={
                        "pexels_id": video.get("id"),
                        "photographer": (user or {}).get("name", "Unknown")
                        if isinstance(user, dict) else "Unknown",
                        "pexels_url": video.get("url", ""),
                        "quality": picked.get("quality", ""),
                    },
                ),
            )
        return results
