"""YouTubePublishAdapter — upload finished video to YouTube.

Uses the YouTube Data API v3 ``videos.insert`` endpoint with a
resumable upload. OAuth 2.0 refresh tokens are stored in
``app_settings`` as secrets; refresh is handled silently by
:mod:`google.oauth2.credentials`.

**Cost note (per `feedback_no_paid_apis`):** the YouTube Data API
v3 is FREE — quota is enforced in units (10,000/day default), NOT
dollars. A single ``videos.insert`` costs ~1,600 units, so a single
channel can ship ~6 uploads/day before hitting the quota wall. This
adapter does NOT consume the paid-cloud-LLM budget envelope; it can
ship to operators who have set "no paid APIs" without violating the
rule.

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
import re
import time
from collections.abc import AsyncIterator
from typing import Any

from poindexter.plugins.publish_adapter import PublishResult
from poindexter.services.cost_guard import CostGuard

logger = logging.getLogger(__name__)


_DEFAULT_CATEGORY = "28"  # Science & Technology
_DEFAULT_PRIVACY = "public"
_TOKEN_URI = "https://oauth2.googleapis.com/token"

# ``PublishResult.status`` marker for "the platform says this video does not
# exist on this channel". Distinguishes a permanent verdict from every other
# update failure (scope refusal, quota, transient 5xx), all of which are worth
# retrying and none of which mean the upload is gone.
STATUS_NOT_FOUND = "not_found"
_VIDEO_URL_FMT = "https://www.youtube.com/watch?v={external_id}"
_SCOPES = ["https://www.googleapis.com/auth/youtube.upload"]

# ``youtube.upload`` is INSERT-ONLY. ``videos.update`` (and the ``videos.list``
# read that must precede it) require one of youtube / youtube.force-ssl /
# youtubepartner — verified empirically 2026-08-31 against the live grant,
# which came back ``scope=…/auth/youtube.upload`` alone.
#
# Scopes live in the TOKEN, not in this constant: widening the list here grants
# nothing on its own. An existing refresh token keeps exactly the scopes it was
# consented with, so editing metadata needs a one-time re-consent
# (``poindexter integrations youtube setup --with-update``). Kept as a separate
# opt-in list rather than folded into _SCOPES so an operator who only ever
# uploads still runs least-privilege, per feedback_oauth_scope_hygiene.
_UPDATE_SCOPE = "https://www.googleapis.com/auth/youtube.force-ssl"
_SCOPES_WITH_UPDATE = [*_SCOPES, _UPDATE_SCOPE]

# A playlist created moments ago 404s for a few seconds; its first insert is
# retried this many times, waiting BACKOFF * attempt seconds between tries.
_PLAYLIST_INSERT_ATTEMPTS = 4
_PLAYLIST_INSERT_BACKOFF_S = 2.0

# Read-only channel analytics, for the ``youtube_reporting`` tap (thumbnail
# impressions + click-through from the Reporting API). Opt-in for the same
# reason as the update scope: an install that never measures reach never
# holds it. ``setup --with-analytics`` requests it.
_ANALYTICS_SCOPE = "https://www.googleapis.com/auth/yt-analytics.readonly"


#: YouTube Data API v3 units per call (developers.google.com/youtube/v3/determine_quota_cost).
#: The daily allowance is 10,000 and resets at midnight Pacific; an upload is
#: 1,600 of it, so a backfill that spends the day's quota also blocks uploads.
QUOTA_UNITS = {
    "videos.insert": 1600,
    "videos.list": 1,
    "videos.update": 50,
    "thumbnails.set": 50,
    "captions.list": 50,
    "captions.insert": 400,
    "playlists.list": 1,
    "playlists.insert": 50,
    "playlistItems.list": 1,
    "playlistItems.insert": 50,
}
DAILY_QUOTA_UNITS = 10_000

_QUOTA_EXHAUSTED = re.compile(r"quotaExceeded|dailyLimitExceeded|exceeded your\W.{0,120}?quota", re.IGNORECASE)


def is_quota_exhausted(text: Any) -> bool:
    """True when a Google error says the day's API quota is spent.

    Matched on the message because the reason code is not always present and
    the human text wraps "quota" in a link (``exceeded your <a ...>quota</a>``).
    Seen 2026-10-18, when a caption backfill (450 units a video) spent the day
    and every later call of a playlist backfill failed the same way.
    """
    return bool(_QUOTA_EXHAUSTED.search(str(text or "")))


def describe_thumbnail_error(exc: Exception) -> str:
    """Name the fix for a failed ``thumbnails.set``.

    A 403 there almost always means the channel has not unlocked custom
    thumbnails, which YouTube gates behind phone verification. The upload
    itself succeeds on such a channel, so without this the thumbnail just
    silently never appears.
    """
    text = str(exc)
    low = text.lower()
    if "403" in low and ("permission" in low or "forbidden" in low or "custom video thumbnails" in low):
        return (
            "the channel cannot set custom thumbnails yet — verify it in YouTube "
            "Studio (Settings → Channel → Feature eligibility → phone "
            f"verification). Google said: {text[:300]}"
        )
    return text[:400]


def _is_insufficient_scope(exc: Exception) -> bool:
    """True when a Google API error is the missing-update-scope 403.

    Matched on the response payload rather than the exception type so it works
    whether googleapiclient raises HttpError or the transport surfaces
    something else. Both the HTTP status and a scope-ish reason must be
    present — a plain 403 (e.g. the channel is suspended) is a different
    problem and must not be mislabelled as "go re-consent".
    """
    lowered = str(exc).lower()
    # Refresh-time shape. Google rejects the token exchange itself with
    # ``invalid_scope`` (no HTTP status in the message) when the requested
    # scopes exceed the grant. Checked FIRST because it carries no 403/401 and
    # the status-code branch below would miss it — which is exactly what
    # happened the first time this ran against the live token.
    if "invalid_scope" in lowered:
        return True
    # Call-time shape: the API itself refuses the request.
    if "403" not in lowered and "401" not in lowered:
        return False
    return any(
        marker in lowered
        for marker in ("insufficientpermissions", "insufficient authentication",
                       "request had insufficient", "scope")
    )



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

    async def _check_gating(
        self,
        *,
        force: bool = False,
    ) -> tuple[bool, str | None, dict[str, str]]:
        """Resolve enabled flag + OAuth secrets.

        Returns ``(ready, error_msg_when_not_ready, secrets_dict)``.
        Centralized here because both ``publish()`` and ``status()``
        need the same gating logic before talking to Google.

        ``force=True`` bypasses the enabled-flag check (but still
        requires all three OAuth secrets). This is the smoke-test
        escape hatch used by ``poindexter integrations youtube test``
        — it lets the operator dry-run an upload end-to-end while the
        adapter is still officially disabled in app_settings, so they
        can verify the wiring before flipping the live switch.
        """
        if not force and not bool(self._get("enabled", False)):
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
            # scopes=None ON PURPOSE — do not "helpfully" name a scope list here.
            #
            # google-auth does NOT treat this as a local hint: it forwards the
            # list as the ``scope`` parameter of the refresh request, and Google
            # rejects the WHOLE refresh with ``invalid_scope`` when it exceeds
            # what the token was granted. Naming the update superset here (the
            # first cut of #3518) therefore broke token refresh outright —
            # uploads included, not just updates — for a channel whose consent
            # was upload-only. Verified against the live token: upload-only list
            # → refresh OK, superset list → RefreshError(invalid_scope),
            # None → refresh OK.
            #
            # Omitting it is also the only value correct in BOTH states: Google
            # returns a token carrying everything the refresh token actually
            # holds, so uploads keep working today and videos.update starts
            # working the moment the operator re-consents — with no second edit
            # here. Pinning a subset would instead mint a token missing
            # force-ssl and 403 the update forever.
            scopes=None,
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

        # Gating — disabled OR missing secrets → fail loudly. The
        # ``_force`` kwarg (set by ``poindexter integrations youtube
        # test``) bypasses the enabled-flag check so the operator can
        # validate the OAuth wiring end-to-end before flipping the
        # adapter live in app_settings.
        force = bool(kwargs.get("_force", False))
        ready, error, secrets = await self._check_gating(force=force)
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

        # YouTube Shorts classification: when the caller flags this upload as
        # a Short (9:16 vertical, ≤60s — the render pipeline already produces
        # 1080×1920), YouTube keys off a ``#Shorts`` marker in the title or
        # description. Append it idempotently (case-insensitive — never a
        # second copy) while honouring the 5000-char description cap by
        # trimming the body to leave room for the marker.
        if bool(kwargs.get("shorts", False)) and "#shorts" not in description_clean.lower():
            shorts_marker = "\n\n#Shorts"
            description_clean = (
                description_clean[: 5000 - len(shorts_marker)].rstrip() + shorts_marker
            )

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
        # Altered/synthetic-content disclosure (YouTube requires it for a
        # realistic synthetic person — our photoreal presenter shots). The
        # caller derives it from the post's shot list + persona policy
        # (``media_distribute``); None leaves the field off the request so
        # older callers keep their exact body.
        synthetic = kwargs.get("contains_synthetic_media")
        if synthetic is not None:
            body["status"]["containsSyntheticMedia"] = bool(synthetic)
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

        # Custom thumbnail (best-effort, only on successful upload). The
        # outcome rides back on PublishResult.metadata["thumbnail"] so the
        # caller can surface a failure: a logged warning alone meant an
        # unverified channel would never show a single custom thumbnail
        # while every upload read as a success.
        thumbnail_status = "not requested"
        if success and thumbnail_path:
            if not os.path.exists(thumbnail_path):
                thumbnail_status = f"failed: thumbnail file missing ({thumbnail_path})"
            else:
                try:
                    await asyncio.to_thread(
                        self._set_thumbnail_blocking,
                        credentials=credentials,
                        video_id=str(response.get("id") or ""),
                        thumbnail_path=thumbnail_path,
                    )
                    thumbnail_status = "set"
                except Exception as exc:
                    thumbnail_status = f"failed: {describe_thumbnail_error(exc)}"
                    logger.warning(
                        "[publish.youtube] thumbnail set failed (upload "
                        "succeeded): %s", thumbnail_status,
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
                "thumbnail": thumbnail_status,
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
        mimetype = "image/png" if thumbnail_path.lower().endswith(".png") else "image/jpeg"
        media = MediaFileUpload(thumbnail_path, mimetype=mimetype)
        youtube.thumbnails().set(videoId=video_id, media_body=media).execute()

    @staticmethod
    def _do_update_metadata_blocking(
        *,
        credentials: Any,
        video_id: str,
        title: str | None,
        description: str | None,
        tags: list[str] | None,
    ) -> dict[str, Any]:
        """Read-modify-write the video's snippet. Runs in a worker thread.

        **``videos.update`` REPLACES the whole part it is given.** Any mutable
        snippet field left out of the request is reset to its default — so
        sending only ``description`` would silently blank the title, tags and
        categoryId of a live video. The read comes first for that reason, not
        as an optimisation: we fetch the current snippet, overlay only the
        fields the caller actually passed, and write the merged result back.

        ``title`` and ``categoryId`` are additionally REQUIRED by the API on
        any snippet update, which the merge satisfies by construction.

        Quota: videos.list = 1 unit, videos.update = 50. Two orders of
        magnitude under the 1,600-unit insert, so a full-channel metadata
        resync is quota-cheap.
        """
        from googleapiclient.discovery import build  # type: ignore[import-not-found]

        youtube = build(
            "youtube", "v3", credentials=credentials, cache_discovery=False,
        )

        current = youtube.videos().list(part="snippet", id=video_id).execute()
        items = current.get("items") or []
        if not items:
            raise LookupError(
                f"video {video_id!r} not found on this channel — it may have "
                "been deleted, or the token belongs to a different account"
            )
        snippet = dict(items[0].get("snippet") or {})

        # Overlay only what the caller supplied; everything else rides through
        # from the live snippet untouched.
        if title is not None:
            snippet["title"] = title.strip()[:100]
        if description is not None:
            snippet["description"] = description.strip()[:5000]
        if tags is not None:
            snippet["tags"] = [str(t).strip() for t in tags if str(t).strip()][:30]

        # Drop read-only members the API rejects on write-back.
        for read_only in ("channelId", "channelTitle", "publishedAt", "thumbnails", "localized"):
            snippet.pop(read_only, None)

        return youtube.videos().update(
            part="snippet", body={"id": video_id, "snippet": snippet},
        ).execute()

    async def set_thumbnail(
        self, *, video_id: str, thumbnail_path: str, force: bool = False,
    ) -> tuple[bool, str]:
        """Set the custom thumbnail of an already-published video.

        Returns ``(ok, detail)``: ``(True, "set")`` or ``(False, why)`` with
        the fix named when Google refused (see ``describe_thumbnail_error``).
        ``thumbnails.set`` needs only the upload scope.
        """
        if not video_id:
            return False, "no video id"
        if not thumbnail_path or not os.path.exists(thumbnail_path):
            return False, f"thumbnail file missing ({thumbnail_path})"
        ready, error, secrets = await self._check_gating(force=force)
        if not ready:
            return False, str(error or "youtube adapter not ready")
        credentials = self._build_credentials(secrets)
        try:
            await asyncio.to_thread(
                self._set_thumbnail_blocking,
                credentials=credentials, video_id=video_id, thumbnail_path=thumbnail_path,
            )
        except Exception as exc:  # noqa: BLE001 — the caller reports it per video
            return False, describe_thumbnail_error(exc)
        return True, "set"

    @staticmethod
    def _set_captions_blocking(
        *,
        credentials: Any,
        video_id: str,
        srt_path: str,
        language: str,
        name: str,
    ) -> dict[str, Any]:
        """Add a caption track unless one with this language and name exists.

        ``captions.list`` (50 units) first, so a re-run never stacks a second
        copy of our track; YouTube's own speech track (``trackKind='asr'``) is
        ignored. ``captions.insert`` is 400 units and needs the
        ``youtube.force-ssl`` scope (``setup --with-update``).
        Returns ``{"id": ..., "existing": bool}``.
        """
        from googleapiclient.discovery import build  # type: ignore[import-not-found]
        from googleapiclient.http import MediaFileUpload  # type: ignore[import-not-found]

        youtube = build("youtube", "v3", credentials=credentials, cache_discovery=False)
        listed = youtube.captions().list(part="snippet", videoId=video_id).execute()
        for item in listed.get("items") or []:
            snip = item.get("snippet") or {}
            if (
                str(snip.get("trackKind") or "").lower() != "asr"
                and str(snip.get("language") or "").lower() == language.lower()
                and str(snip.get("name") or "") == name
            ):
                return {"id": item.get("id"), "existing": True}
        media = MediaFileUpload(srt_path, mimetype="application/octet-stream", resumable=False)
        created = youtube.captions().insert(
            part="snippet",
            body={"snippet": {"videoId": video_id, "language": language, "name": name, "isDraft": False}},
            media_body=media,
        ).execute()
        return {"id": created.get("id"), "existing": False}

    async def set_captions(
        self, *, video_id: str, srt_path: str, language: str, name: str, force: bool = False,
    ) -> tuple[bool, str]:
        """Attach an SRT caption track to an already-published video.

        Returns ``(ok, detail)``: ``(True, "set")``, ``(True, "already there")``
        or ``(False, why)``, naming the re-consent command when the token lacks
        ``youtube.force-ssl``.
        """
        if not video_id:
            return False, "no video id"
        if not srt_path or not os.path.exists(srt_path):
            return False, f"caption file missing ({srt_path})"
        ready, error, secrets = await self._check_gating(force=force)
        if not ready:
            return False, str(error or "youtube adapter not ready")
        credentials = self._build_credentials(secrets)
        try:
            result = await asyncio.to_thread(
                self._set_captions_blocking,
                credentials=credentials, video_id=video_id, srt_path=srt_path,
                language=language, name=name,
            )
        except Exception as exc:  # noqa: BLE001 — the caller reports it per video
            if _is_insufficient_scope(exc):
                return False, (
                    "the token lacks youtube.force-ssl, which captions need — run "
                    "`poindexter integrations youtube setup --with-update`. "
                    f"Google said: {str(exc)[:300]}"
                )
            return False, str(exc)[:400]
        return True, "already there" if result.get("existing") else "set"

    @staticmethod
    def _add_to_playlists_blocking(
        *,
        credentials: Any,
        video_id: str,
        playlists: list[tuple[str, str]],
        privacy: str,
    ) -> dict[str, str]:
        """Put the video in each ``(title, description)`` playlist, creating any missing.

        Playlists are found by exact title among the channel's own
        (``playlists.list mine=true``, 1 unit a page) and created when absent
        (``playlists.insert``, 50). Membership is checked first
        (``playlistItems.list`` filtered by ``videoId``, 1), so a re-run never
        adds the video twice; ``playlistItems.insert`` is 50. Needs
        ``youtube.force-ssl``. Returns ``{title: "added" | "already in"}``.
        """
        from googleapiclient.discovery import build  # type: ignore[import-not-found]

        youtube = build("youtube", "v3", credentials=credentials, cache_discovery=False)
        existing: dict[str, str] = {}
        page_token = None
        while True:
            kwargs: dict[str, Any] = {"part": "snippet", "mine": True, "maxResults": 50}
            if page_token:
                kwargs["pageToken"] = page_token
            resp = youtube.playlists().list(**kwargs).execute()
            for item in resp.get("items") or []:
                title = str((item.get("snippet") or {}).get("title") or "")
                existing.setdefault(title, str(item.get("id")))
            page_token = resp.get("nextPageToken")
            if not page_token:
                break
        outcome: dict[str, str] = {}
        for title, description in playlists:
            playlist_id = existing.get(title)
            created_now = False
            if not playlist_id:
                created = youtube.playlists().insert(
                    part="snippet,status",
                    body={
                        "snippet": {"title": title[:150], "description": description[:5000]},
                        "status": {"privacyStatus": privacy},
                    },
                ).execute()
                playlist_id = str(created.get("id"))
                existing[title] = playlist_id
                created_now = True
            # A playlist created a moment ago is empty, and YouTube answers 404
            # for it for a few seconds (seen live 2026-10-18), so it skips the
            # membership check and its insert retries a not-found briefly.
            if not created_now:
                member = youtube.playlistItems().list(
                    part="id", playlistId=playlist_id, videoId=video_id, maxResults=1,
                ).execute()
                if member.get("items"):
                    outcome[title] = "already in"
                    continue
            body = {"snippet": {"playlistId": playlist_id, "resourceId": {"kind": "youtube#video", "videoId": video_id}}}
            for attempt in range(_PLAYLIST_INSERT_ATTEMPTS):
                try:
                    youtube.playlistItems().insert(part="snippet", body=body).execute()
                    break
                except Exception as exc:  # noqa: BLE001 — only a fresh playlist's not-found is retried
                    if not created_now or "404" not in str(exc) or attempt == _PLAYLIST_INSERT_ATTEMPTS - 1:
                        raise
                    time.sleep(_PLAYLIST_INSERT_BACKOFF_S * (attempt + 1))
            outcome[title] = "added"
        return outcome

    async def add_to_playlists(
        self, *, video_id: str, playlists: list[tuple[str, str]], privacy: str = "public", force: bool = False,
    ) -> tuple[bool, str, dict[str, str]]:
        """Add an already-published video to playlists by title.

        Returns ``(ok, detail, {title: status})``; ``detail`` names the
        re-consent command when the token lacks ``youtube.force-ssl``.
        """
        if not video_id:
            return False, "no video id", {}
        if not playlists:
            return True, "no playlist matched", {}
        ready, error, secrets = await self._check_gating(force=force)
        if not ready:
            return False, str(error or "youtube adapter not ready"), {}
        credentials = self._build_credentials(secrets)
        try:
            outcome = await asyncio.to_thread(
                self._add_to_playlists_blocking,
                credentials=credentials, video_id=video_id, playlists=playlists, privacy=privacy,
            )
        except Exception as exc:  # noqa: BLE001 — the caller reports it per video
            if _is_insufficient_scope(exc):
                return False, (
                    "the token lacks youtube.force-ssl, which playlists need — run "
                    "`poindexter integrations youtube setup --with-update`. "
                    f"Google said: {str(exc)[:300]}"
                ), {}
            return False, str(exc)[:400], {}
        return True, "ok", outcome

    async def update_metadata(
        self,
        *,
        video_id: str,
        title: str | None = None,
        description: str | None = None,
        tags: list[str] | None = None,
        **kwargs: Any,
    ) -> PublishResult:
        """Update an already-published video's title / description / tags.

        Only the fields passed as non-``None`` are changed; the rest of the
        live snippet is preserved (see the read-modify-write note on
        :meth:`_do_update_metadata_blocking`).

        Returns a ``PublishResult`` mirroring :meth:`publish` so callers get
        one shape from both paths. An insufficient-scope 403 — the expected
        first failure, because the original consent was upload-only — comes
        back with the exact remediation command rather than a raw Google
        error.
        """
        force = bool(kwargs.get("_force", False))
        ready, error, secrets = await self._check_gating(force=force)
        if not ready:
            return PublishResult(platform=self.name, success=False, error=error)

        if not video_id:
            return PublishResult(
                platform=self.name, success=False, error="video_id is required",
            )
        if title is None and description is None and tags is None:
            return PublishResult(
                platform=self.name,
                success=False,
                error="nothing to update — pass at least one of title/description/tags",
            )

        try:
            credentials = self._build_credentials(secrets)
            await asyncio.to_thread(
                self._do_update_metadata_blocking,
                credentials=credentials,
                video_id=video_id,
                title=title,
                description=description,
                tags=tags,
            )
        except LookupError as exc:
            # The video is not on this channel — a verdict, not a window. Flagged
            # structurally (``status``) rather than left for the caller to
            # string-match the message, because acting on it means demoting a
            # durable row: ``youtube_metadata_sync`` marks the distribution
            # ``status='deleted'`` so a vanished upload stops being retried
            # forever and stops counting as published.
            return PublishResult(
                platform=self.name,
                success=False,
                status=STATUS_NOT_FOUND,
                error=str(exc),
            )
        except Exception as exc:  # noqa: BLE001 — classified below, never swallowed
            message = str(exc)
            if _is_insufficient_scope(exc):
                return PublishResult(
                    platform=self.name,
                    success=False,
                    error=(
                        "YouTube refused the update: the stored refresh token "
                        "was granted 'youtube.upload', which is INSERT-ONLY. "
                        "videos.update needs the "
                        f"'{_UPDATE_SCOPE}' scope. Re-consent once with "
                        "`poindexter integrations youtube setup --with-update` "
                        "(opens a browser; replaces the stored refresh token), "
                        "then re-run. Underlying error: " + message
                    ),
                )
            logger.warning(
                "[YOUTUBE] videos.update failed for %s: %s", video_id, message,
            )
            return PublishResult(
                platform=self.name,
                success=False,
                error=f"videos.update failed: {type(exc).__name__}: {message}",
            )

        return PublishResult(
            platform=self.name,
            success=True,
            external_id=video_id,
            public_url=f"https://www.youtube.com/watch?v={video_id}",
        )

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
