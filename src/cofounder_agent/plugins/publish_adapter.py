"""PublishAdapter — upload finished media to a third-party platform.

Every "post finished video / image / audio to platform X with platform
auth" pathway hits a third-party API. Wrapping each one as a
PublishAdapter plugin keeps the orchestration layer (the
``upload_to_platform`` Stage in the video pipeline) free of
platform-specific code, gives each adapter a uniform
configuration shape in ``app_settings``, and routes every external
call through the unified cost-guard so cost / electricity tracking
captures upload bandwidth and API billing where applicable.

Two implementation styles, both shipping the same Protocol:

- **Cloud-API adapters** (YouTube Data API v3, TikTok Login Kit,
  Instagram Graph API, X API v2) — OAuth 2.0 credentials live in
  ``site_config.get_secret`` under
  ``plugin.publish_adapter.<name>.{client_id,client_secret,refresh_token}``.
  Adapter handles token refresh transparently.
- **Webhook adapters** (Discord, Telegram, custom webhook
  endpoints) — single bearer token / webhook URL, simpler config.

Both paths produce the same :class:`PublishResult` so the calling
Stage can fan out to many platforms in parallel and collect the
results uniformly.

Register a PublishAdapter via ``pyproject.toml``:

.. code:: toml

    [project.entry-points."poindexter.publish_adapters"]
    youtube = "cofounder_agent.services.publish_adapters.youtube:YouTubePublishAdapter"
    tiktok  = "cofounder_agent.services.publish_adapters.tiktok:TikTokPublishAdapter"

Per-install config lives in ``app_settings.plugin.publish_adapter.<name>``
— ``enabled``, OAuth credentials (as secrets), default visibility,
default category, etc.

Tracks the gating OAuth ticket (Glad-Labs/poindexter#40) — adapters
ship inert until the operator opts in by writing the secret rows.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from dataclasses import dataclass, field
from typing import Any, Protocol, runtime_checkable


@dataclass
class PublishResult:
    """Outcome of a single upload to one platform.

    Mirrors the field shape adapters in
    ``services/social_adapters/`` already return for blog
    cross-posting, so the consolidated dashboard view doesn't need
    special-casing per asset family.

    Attributes:
        platform: Adapter name (``"youtube"``, ``"tiktok"``,
            ``"reels"``, ``"x"``). Matches the entry_point key.
        success: True when the platform accepted the upload AND
            returned a usable identifier. False on auth failure,
            quota exhaustion, transient 5xx, or content rejection.
        external_id: Platform's persistent identifier for the
            asset (``"abc123"`` for YouTube videoId, etc.). ``None``
            on failure or when the platform hasn't issued one yet
            (some platforms accept the upload async and return the
            ID later via webhook).
        public_url: Caller-renderable URL where the content lives.
            ``None`` until the platform has fully processed the
            upload — adapters that know the eventual URL up-front
            (YouTube) populate immediately; adapters that need
            post-processing (TikTok) leave it ``None`` and rely
            on a follow-up status check.
        status: Free-form platform-side status string
            (``"published"``, ``"processing"``, ``"under_review"``).
            ``""`` when not provided.
        error: Human-readable failure summary. ``None`` on success.
            Adapters MUST NOT include credential material here.
        upload_started_at: Wall-clock when the adapter started the
            POST. ISO 8601 string. Used by the cost dashboard to
            attribute upload bandwidth to the right billing window.
        cost_usd: Operator-facing cost of this upload (API billing,
            bandwidth charges if known). ``0.0`` for free-tier
            uploads. Surfaced via the unified cost-guard.
        metadata: Free-form per-platform data the adapter wants to
            stash for debugging — rate-limit headers, request IDs,
            response shape variants.
    """

    platform: str
    success: bool
    external_id: str | None = None
    public_url: str | None = None
    status: str = ""
    error: str | None = None
    upload_started_at: str = ""
    cost_usd: float = 0.0
    metadata: dict[str, Any] = field(default_factory=dict)


@runtime_checkable
class PublishAdapter(Protocol):
    """Upload finished media to one third-party platform.

    Implementations MUST:

    1. Read their config (OAuth credentials, defaults) from
       ``site_config.get_secret`` / ``site_config.get`` under the
       namespace ``plugin.publish_adapter.<self.name>.*``. Never
       from environment variables, never from disk-level files.
    2. Bail loudly when ``enabled=false`` instead of silently
       no-op'ing — see the discipline in
       ``plugins/llm_providers/anthropic.py`` for the canonical
       shape.
    3. Route every outbound call through the cost-guard
       (``record_usage`` on the call's ``CostGuard`` with
       ``provider=f"publish.{self.name}"``) so bandwidth /
       API-billing show on the eco dashboard. ``is_local`` is
       ``False`` for cloud adapters — every PublishAdapter touches
       a remote service by definition.
    4. Refresh OAuth tokens silently; if a refresh fails, return a
       :class:`PublishResult` with ``success=False`` and a clear
       ``error`` string — never raise out of ``publish()`` on a
       recoverable error.

    Attributes:
        name: Provider name. Matches the entry_point key
            (``"youtube"``, ``"tiktok"``, etc.).
        supports_short: True when the platform accepts vertical
            < 60s clips (YouTube Shorts, TikTok, Instagram Reels).
        supports_long: True when the platform accepts landscape
            long-form (YouTube main, X for ≤2:20 video).
    """

    name: str
    supports_short: bool
    supports_long: bool

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
        """Upload one finished media file to the platform.

        Args:
            media_path: Local filesystem path to the finished media
                (MP4 for video adapters; PNG/JPG for image-only
                platforms; MP3 for audio-only).
            title: Platform-side title. Adapters truncate to the
                platform's limit (YouTube: 100 chars).
            description: Long-form description / caption / body
                copy. Adapters truncate to the platform's limit.
            tags: Optional list of tags / hashtags.
            thumbnail_path: Optional path to a custom thumbnail
                image. Platforms that don't support custom
                thumbnails ignore this.
            scheduled_at: ISO 8601 timestamp for delayed
                publishing. ``None`` means publish immediately.
                Adapters that don't support scheduling ignore.
            **kwargs: Platform-specific extras (``category_id``,
                ``privacy``, ``language``, etc.). Adapters MUST
                NOT raise on unknown kwargs — silently ignore.

        Returns:
            :class:`PublishResult` describing the outcome.
            Adapters return success=False inside the result instead
            of raising for any recoverable error.
        """
        ...

    async def status(
        self,
        external_id: str,
    ) -> PublishResult:
        """Check the platform-side status of a previously-uploaded
        asset by external_id.

        Useful for adapters where ``publish()`` returns
        ``status="processing"`` and the eventual public URL only
        materializes once the platform finishes encoding /
        moderation. The video pipeline polls this on a backoff
        until ``status=="published"`` or the wait budget elapses.

        Adapters whose platforms don't expose a status endpoint
        return the cached ``PublishResult`` from the original
        upload unchanged.
        """
        ...

    async def stream_progress(
        self,
        external_id: str,
    ) -> AsyncIterator[PublishResult]:
        """Optional: yield ``PublishResult`` snapshots as platform-
        side processing advances.

        Adapters that don't support real-time progress (YouTube)
        simply ``yield`` once and stop. Adapters that do (some
        custom platforms via webhooks) yield per state change.

        Default implementations of this method may simply call
        :meth:`status` once. The Protocol declares it so callers
        can opt in to a streaming UI without breaking adapters
        that only support polling.
        """
        ...
