"""Rebuild — and reconcile — a media RSS feed on R2 from the worker's feed route.

Shared, idempotent + non-fatal helpers. Media (podcast / video) is approved
*after* the post publishes, but the R2 copy of a feed is only rebuilt at
publish time (``publish_service``) — so between publish and approval the feed
is stale and an approval never propagates until some *later* publish. That's
the mechanism behind the 2026-05-27→06-13 feed freeze recurring even once
assets were seeded.

``media_approval_service.decide`` calls :func:`rebuild_feed_for_medium` on
approve so the approval reaches Apple / Spotify / the video feed immediately.

Lifted from ``services.jobs.podcast_distribute._rebuild_feed`` (#689) and
generalized to podcast + video so there's one rebuild seam, not three copies
(``feedback_no_wheel_reinvention``).

## Why an event-coupled rebuild isn't enough

Every rebuild trigger is a discrete event, and every one of them swallows its
own failures:

- ``publish_service`` — fires at publish, *before* the medium is approved, so
  it can never include the post it fired for.
- ``decide()`` — only when the caller threads a ``site_config`` through.
- ``podcast_distribute`` Pass 3 — only when that cycle delivered a *new*
  approved-and-undispatched asset; an already-dispatched one never re-fires.
- ``media_distribute`` (video) — historically fired nothing at all.

Miss one and the published feed stays wrong until an unrelated later event
happens to rebuild it. On 2026-07-18 the podcast feed on R2 held **71**
episodes while the DB was eligible for **100** — a 29-episode backlog that
survived weeks of publishes.

:func:`reconcile_feed` closes that class for good by making the feed
*convergent* rather than event-driven: render the feed from the DB, compare it
to the published object, and republish when they differ. State, not events.

## The shrink guard

``podcast_feed`` catches its own DB error and returns a **valid zero-item
feed**. A convergence loop that trusted that render would wipe every episode
off Apple/Spotify — turning a staleness bug into an outage. So the loop is
deliberately asymmetric: it will grow a feed freely, but refuses to shrink one
by more than ``media_feed_reconcile_max_shrink`` items and escalates instead
(``feedback_self_heal_not_suppress`` — self-heal, but never self-harm).
"""
from __future__ import annotations

import logging
import os
import re
import tempfile
from dataclasses import dataclass
from typing import Any

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class _FeedSpec:
    """Where a medium's feed is rendered from and published to."""

    route: str
    r2_path: str
    label: str


# The two media that have an RSS surface. ``video_short`` is dispatched to
# YouTube Shorts and has no feed, so it is deliberately absent.
_FEED_SPECS: dict[str, _FeedSpec] = {
    "podcast": _FeedSpec("/api/podcast/feed.xml", "podcast/feed.xml", "podcast"),
    "video": _FeedSpec("/api/video/feed.xml", "video/feed.xml", "video"),
}

#: Media the reconciliation watchdog walks each cycle.
RECONCILABLE_MEDIA: tuple[str, ...] = tuple(_FEED_SPECS)

# ``<item>`` / ``<item ...>`` but never ``<itunes:image>`` — the 4th character
# disambiguates (``item`` vs ``itun``), so a cheap scan is exact here and stays
# total on malformed XML where a real parse would raise.
_ITEM_RE = re.compile(r"<item[\s>/]")

# Default ceiling on how many items a single reconcile may remove from the
# published feed before it refuses and escalates. Tunable per operator via
# ``app_settings.media_feed_reconcile_max_shrink``.
_DEFAULT_MAX_SHRINK = 5


@dataclass(frozen=True)
class FeedReconcileResult:
    """Outcome of one :func:`reconcile_feed` pass.

    ``drifted`` means published != rendered. ``healed`` means we republished.
    ``refused`` means we found drift but declined to publish it because the
    render would have shrunk the feed past the guard — the interesting state,
    since it points at the *renderer* being broken rather than the feed.
    """

    medium: str
    rendered_items: int
    published_items: int
    drifted: bool
    healed: bool
    refused: bool = False
    error: str | None = None


def count_feed_items(xml: str | None) -> int:
    """Number of ``<item>`` elements in an RSS body. Missing/garbage → ``0``.

    Total by construction: a published object that failed to download, or that
    is truncated, reads as an empty feed — which the caller correctly treats as
    drift worth republishing.
    """
    if not xml:
        return 0
    return len(_ITEM_RE.findall(xml))


async def _fetch_rendered_feed(site_config: Any, route: str) -> str | None:
    """GET ``{internal_api_base_url}{route}`` — the feed as the DB currently
    defines it. Returns ``None`` when the worker is unreachable."""
    try:
        import httpx

        from services.bootstrap_defaults import DEFAULT_WORKER_API_URL

        api_base = site_config.get("internal_api_base_url", DEFAULT_WORKER_API_URL)
        async with httpx.AsyncClient(
            timeout=httpx.Timeout(30.0, connect=5.0)
        ) as client:
            feed = await client.get(f"{api_base}{route}", timeout=30)
        return feed.text
    except Exception as exc:  # noqa: BLE001 — caller decides; never raise upward
        logger.warning(
            "[MEDIA_FEED_REBUILD] could not render %s: %s", route, exc,
        )
        return None


async def _upload_feed(
    site_config: Any, body: str, *, r2_path: str, label: str,
) -> bool:
    """Write ``body`` to a temp file and upload it to R2 ``r2_path``."""
    try:
        from services.r2_upload_service import R2UploadService

        fd, feed_path = tempfile.mkstemp(suffix=".xml", prefix="poindexter-feed-")
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                f.write(body)
            await R2UploadService(site_config=site_config).upload_to_r2(
                feed_path, r2_path, "application/rss+xml",
            )
            logger.info(
                "[MEDIA_FEED_REBUILD] %s RSS feed rebuilt on R2 (%s)", label, r2_path,
            )
            return True
        finally:
            try:
                os.unlink(feed_path)
            except OSError:  # silent-ok: — best-effort temp-file cleanup
                pass
    except Exception as exc:  # noqa: BLE001 — feed upload is non-fatal
        logger.warning(
            "[MEDIA_FEED_REBUILD] %s feed upload failed (non-fatal): %s", label, exc,
        )
        return False


async def _read_published_feed(site_config: Any, r2_path: str) -> str | None:
    """Read the currently-published feed object straight from the bucket.

    Deliberately an authenticated S3 ``GetObject`` rather than a fetch of the
    public CDN URL: ``pub-*.r2.dev`` caches, and comparing against a cached copy
    would manufacture phantom drift (and an R2 write) on every cycle.
    """
    try:
        from services.r2_upload_service import R2UploadService

        return await R2UploadService(site_config=site_config).get_object_text(r2_path)
    except Exception as exc:  # noqa: BLE001 — absent object is a valid answer
        logger.warning(
            "[MEDIA_FEED_REBUILD] could not read published %s: %s", r2_path, exc,
        )
        return None


def _max_shrink(site_config: Any) -> int:
    try:
        raw = site_config.get(
            "media_feed_reconcile_max_shrink", str(_DEFAULT_MAX_SHRINK),
        )
        return max(0, int(raw))
    except (TypeError, ValueError):
        return _DEFAULT_MAX_SHRINK


async def _rebuild_feed(
    site_config: Any, *, route: str, r2_path: str, label: str,
) -> None:
    """Render ``route`` → upload the body to R2 ``r2_path``.

    Non-fatal: any failure (worker unreachable, R2 unconfigured, upload error)
    is logged and swallowed — the rebuild is additive self-healing, never part
    of the approval transaction.
    """
    body = await _fetch_rendered_feed(site_config, route)
    if body is None:
        return
    await _upload_feed(site_config, body, r2_path=r2_path, label=label)


async def rebuild_podcast_feed(site_config: Any) -> None:
    """Rebuild ``podcast/feed.xml`` on R2 from ``/api/podcast/feed.xml``."""
    spec = _FEED_SPECS["podcast"]
    await _rebuild_feed(
        site_config, route=spec.route, r2_path=spec.r2_path, label=spec.label,
    )


async def rebuild_video_feed(site_config: Any) -> None:
    """Rebuild ``video/feed.xml`` on R2 from ``/api/video/feed.xml``."""
    spec = _FEED_SPECS["video"]
    await _rebuild_feed(
        site_config, route=spec.route, r2_path=spec.r2_path, label=spec.label,
    )


async def rebuild_feed_for_medium(site_config: Any, medium: str) -> None:
    """Rebuild the R2 RSS feed that surfaces an approved ``medium``.

    - ``podcast`` → podcast feed
    - ``video`` → video feed (long-form RSS)
    - ``video_short`` → **no-op**: shorts are dispatched to YouTube Shorts by
      ``media_distribute``, they have no RSS feed surface to rebuild.
    """
    if medium == "podcast":
        await rebuild_podcast_feed(site_config)
    elif medium == "video":
        await rebuild_video_feed(site_config)
    # video_short: no RSS surface — intentionally nothing to rebuild.


async def reconcile_feed(site_config: Any, medium: str) -> FeedReconcileResult:
    """Converge the published feed for ``medium`` onto the DB's current truth.

    Renders the feed, reads the published object, and republishes when they
    differ — so the feed self-corrects regardless of which event-coupled
    rebuild was missed. Two refusals keep the loop safe:

    - a render we couldn't obtain is never published (nothing to converge on);
    - a render that would shrink the feed past ``media_feed_reconcile_max_shrink``
      is never published, because the likeliest cause is a broken renderer (the
      feed route returns a valid *empty* feed when its DB query fails), not 90
      episodes genuinely disappearing.

    Never raises for operational failures — the caller is a scheduled watchdog.
    An unknown ``medium`` *does* raise: that's a programming error, and failing
    loud beats silently reconciling nothing (``feedback_no_silent_defaults``).
    """
    spec = _FEED_SPECS.get(medium)
    if spec is None:
        raise ValueError(
            f"No RSS surface for medium {medium!r}; reconcilable media are "
            f"{list(RECONCILABLE_MEDIA)}.",
        )

    try:
        rendered = await _fetch_rendered_feed(site_config, spec.route)
        if rendered is None:
            return FeedReconcileResult(
                medium=medium, rendered_items=0, published_items=0,
                drifted=False, healed=False,
                error="feed route unreachable — nothing safe to publish",
            )

        published = await _read_published_feed(site_config, spec.r2_path)
        rendered_items = count_feed_items(rendered)
        published_items = count_feed_items(published)

        if published is not None and rendered == published:
            logger.debug(
                "[MEDIA_FEED_RECONCILE] %s in sync (%d items)",
                spec.label, rendered_items,
            )
            return FeedReconcileResult(
                medium=medium, rendered_items=rendered_items,
                published_items=published_items, drifted=False, healed=False,
            )

        shrink = published_items - rendered_items
        limit = _max_shrink(site_config)
        if published_items > 0 and shrink > limit:
            logger.error(
                "[MEDIA_FEED_RECONCILE] REFUSING to publish %s feed: render has "
                "%d items vs %d published (shrink %d > limit %d). The renderer "
                "is the likely fault — published feed left untouched.",
                spec.label, rendered_items, published_items, shrink, limit,
            )
            return FeedReconcileResult(
                medium=medium, rendered_items=rendered_items,
                published_items=published_items, drifted=True, healed=False,
                refused=True,
                error=(
                    f"render would drop {shrink} items (limit {limit}) — "
                    f"refused to publish"
                ),
            )

        healed = await _upload_feed(
            site_config, rendered, r2_path=spec.r2_path, label=spec.label,
        )
        if healed:
            logger.info(
                "[MEDIA_FEED_RECONCILE] %s feed converged: %d published → %d "
                "rendered items",
                spec.label, published_items, rendered_items,
            )
        return FeedReconcileResult(
            medium=medium, rendered_items=rendered_items,
            published_items=published_items, drifted=True, healed=healed,
            error=None if healed else "upload failed",
        )
    except Exception as exc:  # noqa: BLE001 — a watchdog never crashes its cycle
        logger.warning(
            "[MEDIA_FEED_RECONCILE] %s reconcile failed (non-fatal): %s",
            medium, exc,
        )
        return FeedReconcileResult(
            medium=medium, rendered_items=0, published_items=0,
            drifted=False, healed=False, error=str(exc),
        )


__all__ = [
    "RECONCILABLE_MEDIA",
    "FeedReconcileResult",
    "count_feed_items",
    "rebuild_feed_for_medium",
    "rebuild_podcast_feed",
    "rebuild_video_feed",
    "reconcile_feed",
]
