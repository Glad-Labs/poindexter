"""YouTubePublishAdapter — upload finished video to YouTube.

Uses the YouTube Data API v3 ``videos.insert`` endpoint with a
resumable upload. OAuth 2.0 refresh tokens are stored in
``app_settings`` as secrets; refresh is handled silently by
:mod:`google.oauth2.credentials`. Free-tier quota is 10,000 units/day
and a single insert costs ~1,600 units, so a single channel can ship
~6 uploads/day without hitting the wall.

Ships **inert** until the operator opts in:

- ``plugin.publish_adapter.youtube.enabled`` is False by default. The
  adapter returns ``PublishResult(success=False, error="...")``
  rather than touching Google when disabled.
- The required secrets (``client_id``, ``client_secret``,
  ``refresh_token``) gate everything else. Until all three are
  populated the adapter bails loudly with a clear "not configured"
  error — never silently no-ops, per the canonical plugin discipline.

This is the gating tracker on Glad-Labs/poindexter#40 — once Matt has
run the OAuth flow once and seeded the refresh_token, the adapter
flips active and the video pipeline's ``upload_to_platform`` Stage
starts hitting it.

Config (``plugin.publish_adapter.youtube`` in app_settings):

- ``enabled`` (bool, default False) — kill switch.
- ``client_id`` (secret, required) — OAuth 2.0 client ID.
- ``client_secret`` (secret, required) — OAuth 2.0 client secret.
- ``refresh_token`` (secret, required) — refresh token from the
  one-time consent flow.
- ``default_category_id`` (str, default ``"28"`` — Science & Technology).
- ``default_privacy`` (str, default ``"public"``) — one of
  ``"public"``, ``"unlisted"``, ``"private"``.
- ``default_made_for_kids`` (bool, default False).
"""

from __future__ import annotations

import asyncio
import logging
import os
import time
from collections.abc import AsyncIterator
from typing import Any

from plugins.publish_adapter import PublishResult
from services.cost_guard import CostGuard

logger = logging.getLogger(__name__)


_DEFAULT_CATEGORY = "28"  # Science & Technology
_DEFAULT_PRIVACY = "public"
_TOKEN_URI = "https://oauth2.googleapis.com/token"
_VIDEO_URL_FMT = "https://www.youtube.com/watch?v={external_id}"
_SCOPES = ["https://www.googleapis.com/auth/youtube.upload"]


class YouTubePublishAdapter:
    """Publish finished MP4s to YouTube via the Data API v3."""

    name = "youtube"
    supports_short = True  # YouTube Shorts (vertical, ≤60s)
    supports_long = True  # Long-form landscape

    def __init__(self, site_config: Any = None) -> None:
        self._site_config = site_config

    def _get(self, key: str, default: Any) -> Any:
        if self._site_config is None:
            return default
        return self._site_config.get(
            f"plugin.publish_adapter.youtube.{key}",
            default,
        )

    async def _get_secret(self, key: str) -> str:
        """Read an OAuth secret from app_settings.

        Empty string when no SiteConfig is wired (test environment) —
        callers MUST treat empty as "not configured" and bail.
        """
        if self._site_config is None:
            return ""
        try:
            value = await self._site_config.get_secret(
                f"plugin.publish_adapter.youtube.{key}",
                "",
            )
        except Exception as exc:  # secret table may not exist in tests
            logger.warning(
                "[publish.youtube] get_secret(%s) raised: %s", key, exc,
            )
            return ""
        return str(value or "")

    def _build_cost_guard(self, kwargs: dict[str, Any]) -> CostGuard:
        injected = kwargs.get("_cost_guard")
        if isinstance(injected, CostGuard):
            return injected
        site_config = kwargs.get("_site_config", self._site_config)
        pool = kwargs.get("_pool")
        if pool is None and site_config is not None:
            pool = getattr(site_config, "_pool", None)
        return CostGuard(site_config=site_config, pool=pool)

    async def _check_gating(self) -> tuple[bool, str | None, dict[str, str]]:
        """Resolve enabled flag + OAuth secrets.

        Returns ``(ready, error_msg_when_not_ready, secrets_dict)``.
        Centralized here because both ``publish()`` and ``status()``
        need the same gating logic before talking to Google.
        """
        if not bool(self._get("enabled", False)):
            return (
                False,
                "youtube adapter disabled in app_settings (set "
                "plugin.publish_adapter.youtube.enabled=true)",
                {},
            )
        client_id = await self._get_secret("client_id")
        client_secret = await self._get_secret("client_secret")
        refresh_token = await self._get_secret("refresh_token")
        if not client_id or not client_secret or not refresh_token:
            return (
                False,
                "youtube OAuth secrets not configured — see "
                "Glad-Labs/poindexter#40 for the one-time setup. "
                "Required: plugin.publish_adapter.youtube.{client_id,"
                "client_secret,refresh_token} (all is_secret=true).",
                {},
            )
        return (
            True,
            None,
            {
                "client_id": client_id,
                "client_secret": client_secret,
                "refresh_token": refresh_token,
            },
        )

    @staticmethod
    def _build_credentials(secrets: dict[str, str]) -> Any:
        """Construct ``google.oauth2.credentials.Credentials`` from the
        secret triplet.

        Lazy-imported so test collection / unit tests that don't touch
        YouTube don't require the google-auth packages to be installed.
        """
        from google.oauth2.credentials import Credentials  # type: ignore[import-not-found]

        return Credentials(
            token=None,  # forces refresh on first request
            refresh_token=secrets["refresh_token"],
            client_id=secrets["client_id"],
            client_secret=secrets["client_secret"],
            token_uri=_TOKEN_URI,
            scopes=_SCOPES,
        )

    @staticmethod
    def _do_resumable_upload_blocking(
        *,
        credentials: Any,
        media_path: str,
        body: dict[str, Any],
    ) -> dict[str, Any]:
        """Run the resumable upload synchronously in a worker thread.

        Returns the parsed JSON response from videos.insert. Caller
        wraps in :func:`asyncio.to_thread`.
        """
        from googleapiclient.discovery import build  # type: ignore[import-not-found]
        from googleapiclient.http import MediaFileUpload  # type: ignore[import-not-found]

        youtube = build(
            "youtube", "v3",
            credentials=credentials,
            cache_discovery=False,  # avoid the deprecated file cache
        )

        # chunksize=-1 streams the file in one shot; flip to e.g. 8MB
        # if you need progress callbacks. For our V0 we await
        # completion and report the final result, no progress UI.
        media = MediaFileUpload(
            media_path,
            chunksize=-1,
            resumable=True,
            mimetype="video/mp4",
        )

        request = youtube.videos().insert(
            part=",".join(body.keys()),
            body=body,
            media_body=media,
        )

        response = None
        # next_chunk returns (status, response) — when chunksize=-1 the
        # first call uploads everything and returns response.
        while response is None:
            _, response = request.next_chunk()
        return response  # YouTube returns the full Video resource

    async def publish(
        self,
        *,
        media_path: str,
        title: str,
        description: str = "",
        tags: list[str] | None = None,
        thumbnail_path: str | None = None,
        scheduled_at: str | None = None,
        **kwargs: Any,
    ) -> PublishResult:
        upload_started_at = (
            time.strftime("%Y-%m-%dT%H:%M:%S", time.gmtime()) + "Z"
        )

        # Gating — disabled OR missing secrets → fail loudly.
        ready, error, secrets = await self._check_gating()
        if not ready:
            return PublishResult(
                platform=self.name,
                success=False,
                error=error,
                upload_started_at=upload_started_at,
            )

        if not media_path or not os.path.exists(media_path):
            return PublishResult(
                platform=self.name,
                success=False,
                error=f"media_path missing: {media_path!r}",
                upload_started_at=upload_started_at,
            )

        # Truncate to YouTube's documented limits to avoid an API
        # 400 mid-upload — title 100 chars, description 5000 chars,
        # tags 500 chars total (we hard-cap at 30 individual tags).
        title_clean = (title or "").strip()[:100]
        description_clean = (description or "").strip()[:5000]
        tags_clean = [str(t).strip() for t in (tags or []) if str(t).strip()][:30]

        category_id = str(
            kwargs.get("category_id")
            or self._get("default_category_id", _DEFAULT_CATEGORY),
        )
        privacy = str(
            kwargs.get("privacy")
            or self._get("default_privacy", _DEFAULT_PRIVACY),
        )
        made_for_kids = bool(
            kwargs.get("made_for_kids")
            if kwargs.get("made_for_kids") is not None
            else self._get("default_made_for_kids", False)
        )

        body: dict[str, Any] = {
            "snippet": {
                "title": title_clean,
                "description": description_clean,
                "tags": tags_clean,
                "categoryId": category_id,
            },
            "status": {
                "privacyStatus": privacy,
                "selfDeclaredMadeForKids": made_for_kids,
            },
        }
        if scheduled_at:
            # YouTube only schedules when privacyStatus is "private"
            # at upload — flip it for the operator and let the API
            # surface it at publishTime.
            body["status"]["privacyStatus"] = "private"
            body["status"]["publishAt"] = scheduled_at

        # Lazy-import & build creds. Catch ImportError separately so
        # operators get a precise error pointing at the missing
        # dependency rather than a googleapi traceback.
        try:
            credentials = self._build_credentials(secrets)
        except ImportError as exc:
            return PublishResult(
                platform=self.name,
                success=False,
                error=(
                    f"google-auth not installed ({exc}). "
                    "Run: pip install google-auth google-api-python-client"
                ),
                upload_started_at=upload_started_at,
            )

        cost_guard = self._build_cost_guard(kwargs)
        media_size = os.path.getsize(media_path)
        started = time.perf_counter()
        success = True
        error_msg: str | None = None
        response: dict[str, Any] = {}

        try:
            response = await asyncio.to_thread(
                self._do_resumable_upload_blocking,
                credentials=credentials,
                media_path=media_path,
                body=body,
            )
        except ImportError as exc:
            success = False
            error_msg = (
                f"google-api-python-client not installed ({exc}). "
                "Run: pip install google-api-python-client"
            )
        except Exception as exc:
            success = False
            error_msg = f"{type(exc).__name__}: {str(exc)[:500]}"
            logger.exception("[publish.youtube] upload failed")

        duration_ms = int((time.perf_counter() - started) * 1000)

        # Custom thumbnail (best-effort, only on successful upload).
        if success and thumbnail_path and os.path.exists(thumbnail_path):
            try:
                await asyncio.to_thread(
                    self._set_thumbnail_blocking,
                    credentials=credentials,
                    video_id=str(response.get("id") or ""),
                    thumbnail_path=thumbnail_path,
                )
            except Exception as exc:
                logger.warning(
                    "[publish.youtube] thumbnail set failed (upload "
                    "succeeded): %s", exc,
                )

        # Cost-guard. YouTube Data API v3 is free under quota; we
        # record the row anyway with cost=0 so the eco dashboard can
        # show "the upload happened" without dollar attribution.
        try:
            await cost_guard.record_usage(
                provider=f"publish.{self.name}",
                model="videos.insert",
                prompt_tokens=0,
                completion_tokens=0,
                cost_usd=0.0,
                phase=str(kwargs.get("phase", "publish")),
                task_id=kwargs.get("task_id"),
                success=success,
                duration_ms=duration_ms,
                is_local=False,
            )
        except Exception as exc:
            logger.warning("[publish.youtube] cost recording failed: %s", exc)

        external_id = str(response.get("id") or "") or None
        public_url = (
            _VIDEO_URL_FMT.format(external_id=external_id)
            if external_id
            else None
        )
        snippet = response.get("snippet") or {}
        status = response.get("status") or {}
        return PublishResult(
            platform=self.name,
            success=success,
            external_id=external_id,
            public_url=public_url,
            status=str(status.get("uploadStatus") or status.get("privacyStatus") or ""),
            error=error_msg,
            upload_started_at=upload_started_at,
            cost_usd=0.0,
            metadata={
                "duration_ms": duration_ms,
                "file_size_bytes": media_size,
                "category_id": category_id,
                "privacy": privacy,
                "channel_id": str(snippet.get("channelId") or ""),
                "published_at": str(snippet.get("publishedAt") or ""),
                "scheduled_at": scheduled_at or "",
            },
        )

    @staticmethod
    def _set_thumbnail_blocking(
        *,
        credentials: Any,
        video_id: str,
        thumbnail_path: str,
    ) -> None:
        """Upload a custom thumbnail — must be ≤ 2MB, JPG/PNG."""
        from googleapiclient.discovery import build  # type: ignore[import-not-found]
        from googleapiclient.http import MediaFileUpload  # type: ignore[import-not-found]

        youtube = build("youtube", "v3", credentials=credentials, cache_discovery=False)
        media = MediaFileUpload(thumbnail_path, mimetype="image/jpeg")
        youtube.thumbnails().set(videoId=video_id, media_body=media).execute()

    async def status(
        self,
        external_id: str,
    ) -> PublishResult:
        """Re-check a video's processing status by ID."""
        ready, error, secrets = await self._check_gating()
        if not ready:
            return PublishResult(
                platform=self.name,
                success=False,
                external_id=external_id or None,
                error=error,
            )
        if not external_id:
            return PublishResult(
                platform=self.name,
                success=False,
                error="status() requires external_id",
            )

        try:
            credentials = self._build_credentials(secrets)
        except ImportError as exc:
            return PublishResult(
                platform=self.name,
                success=False,
                external_id=external_id,
                error=f"google-auth not installed ({exc})",
            )

        try:
            response = await asyncio.to_thread(
                self._do_status_blocking,
                credentials=credentials,
                video_id=external_id,
            )
        except Exception as exc:
            return PublishResult(
                platform=self.name,
                success=False,
                external_id=external_id,
                error=f"{type(exc).__name__}: {str(exc)[:500]}",
            )

        items = response.get("items") or []
        if not items:
            return PublishResult(
                platform=self.name,
                success=False,
                external_id=external_id,
                error=f"video {external_id} not found (deleted? unauthorized?)",
            )
        item = items[0]
        status_block = item.get("status") or {}
        return PublishResult(
            platform=self.name,
            success=True,
            external_id=external_id,
            public_url=_VIDEO_URL_FMT.format(external_id=external_id),
            status=str(
                status_block.get("uploadStatus")
                or status_block.get("privacyStatus")
                or "",
            ),
            metadata={
                "privacy_status": str(status_block.get("privacyStatus") or ""),
                "upload_status": str(status_block.get("uploadStatus") or ""),
                "processing_status": str(
                    (item.get("processingDetails") or {}).get("processingStatus") or "",
                ),
            },
        )

    @staticmethod
    def _do_status_blocking(
        *,
        credentials: Any,
        video_id: str,
    ) -> dict[str, Any]:
        """Synchronous videos.list wrapper for use in to_thread."""
        from googleapiclient.discovery import build  # type: ignore[import-not-found]

        youtube = build("youtube", "v3", credentials=credentials, cache_discovery=False)
        return youtube.videos().list(
            part="status,processingDetails,snippet",
            id=video_id,
        ).execute()

    async def stream_progress(
        self,
        external_id: str,
    ) -> AsyncIterator[PublishResult]:
        """One-shot status snapshot.

        YouTube doesn't expose a streaming progress endpoint; for
        Protocol compliance we simply yield the current status() once
        and stop. Callers that want polling should drive ``status()``
        themselves on a backoff.
        """
        yield await self.status(external_id)
