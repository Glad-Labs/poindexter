"""Rebuild — and reconcile — a media RSS feed on R2 from the worker's feed route.

Shared, idempotent + non-fatal helpers. Media (podcast / video) is approved
*after* the post publishes, so an approval alone never rebuilds the feed —
before the 2026-06-14 fix below, the R2 copy was only ever rebuilt at *publish*
time (from ``publish_service``, since retired — see the trigger list further
down), leaving the feed stale between publish and approval until some
*later*, unrelated publish happened to refresh it. That was the mechanism
behind the 2026-05-27→06-13 feed freeze recurring even once assets were
seeded.

``media_approval_service.decide`` calls :func:`rebuild_feed_for_medium` on
approve so the approval reaches Apple / Spotify / the video feed immediately.

Lifted from ``services.jobs.podcast_distribute._rebuild_feed`` (#689) and
generalized to podcast + video so there's one rebuild seam, not three copies
(``feedback_no_wheel_reinvention``).

## Why an event-coupled rebuild isn't enough

Every rebuild trigger is a discrete event, and every one of them swallows its
own failures:

- ``decide()`` — only when the caller threads a ``site_config`` through.
- ``podcast_distribute`` Pass 3 — only when that cycle delivered a *new*
  approved-and-undispatched asset; an already-dispatched one never re-fires.
- ``media_distribute`` (video) — historically fired nothing at all.

(``publish_service`` used to be a fourth trigger — fired at publish, *before*
the medium is approved, so it could never include the post it fired for
anyway. Its tail was retired 2026-09-25: it was unreachable from the default
approve→stage→promote flow and had done no work in prod in 30+ days. See
``jobs/podcast_distribute.py`` / ``jobs/media_distribute.py`` for who owns
podcast/video delivery now.)

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

## Non-2xx renders are not renders

A feed route can also answer with a non-2xx status instead of a zero-item
feed: ``_r2_url_or_503`` (both routes) 503s when ``storage_public_url`` is
unset, and ``_site_url``/``site_config.require("site_url")`` raises when
``site_url`` is missing, which the app's generic exception handler turns into
a 500. Neither is XML. :func:`_fetch_rendered_feed` checks the status code and
never hands a non-2xx body to a caller as if it were a render — every caller
used to upload that error body to R2 as the feed itself, whatever it happened
to contain (an HTTPException's JSON detail, or a 500 handler's JSON envelope).

The two callers diverge on purpose. The event-coupled rebuilds
(:func:`rebuild_podcast_feed` / :func:`rebuild_video_feed`) just skip the
upload, same as an unreachable worker — they already swallow every failure
silently, and the reconciler is the backstop that notices. :func:`reconcile_feed`
does not stay quiet: a non-2xx response means the route itself is broken (not
merely stale), which is strictly more actionable than "nothing changed," so it
refuses to publish and reports it through the same escalation path as a
collapsed render (``media_feed_render_collapse``) rather than being swallowed
as "unreachable." An unreachable worker still reports nothing safe to publish
without a finding — that case hasn't changed, because a worker that is briefly
unreachable during a restart is not itself an operator-actionable signal the
way a misconfigured route is.
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
    ``refused`` means we declined to publish for one of two reasons, both
    pointing at the *renderer* being broken rather than the published feed:
    the render would have shrunk the feed past the guard, or the feed route
    answered with a non-2xx status instead of a render. ``status_code`` is
    set only for the second case (``None`` for a shrink refusal, and for
    every non-refused outcome) — the caller uses it to tell the two apart.
    """

    medium: str
    rendered_items: int
    published_items: int
    drifted: bool
    healed: bool
    refused: bool = False
    error: str | None = None
    status_code: int | None = None


def _normalize_for_compare(xml: str | None) -> str:
    """Newline- and trailing-whitespace-insensitive view of a feed body.

    Drift means *content* drift. Measured on live prod 2026-07-18: the published
    ``video/feed.xml`` carried ``\\r\\n`` in its XML declaration — written at some
    point by a host-side (Windows) writer doing text-mode newline translation —
    while the container renders ``\\n``. Identical 64 guids, identical content,
    two bytes apart.

    A raw byte-compare would call that drift on every cycle forever: a needless
    R2 write every 15 minutes and a recurring ``media_feed_drift`` finding. That
    is precisely the phantom-drift trap this module avoids for CDN reads, so the
    same discipline applies to the comparison itself. Republishing to "fix" the
    newlines would also risk ping-ponging with whatever host path wrote them.
    """
    if not xml:
        return ""
    return xml.replace("\r\n", "\n").replace("\r", "\n").strip()


def count_feed_items(xml: str | None) -> int:
    """Number of ``<item>`` elements in an RSS body. Missing/garbage → ``0``.

    Total by construction: a published object that failed to download, or that
    is truncated, reads as an empty feed — which the caller correctly treats as
    drift worth republishing.
    """
    if not xml:
        return 0
    return len(_ITEM_RE.findall(xml))


@dataclass(frozen=True)
class _FeedFetch:
    """Outcome of one GET against a worker feed route.

    ``status_code`` is ``None`` only when the request itself never completed
    (connection refused, timeout, DNS failure, ...) — the worker is
    unreachable, and there is nothing to distinguish beyond that. Any other
    value means the route answered: ``body`` is the response text on 2xx,
    and ``None`` on every other status, so a non-2xx body (an HTTPException's
    JSON detail, a 500 handler's error envelope) can never be mistaken for a
    render by a caller that only checks ``body is None``.
    """

    body: str | None
    status_code: int | None


async def _fetch_rendered_feed(site_config: Any, route: str) -> _FeedFetch:
    """GET ``{internal_api_base_url}{route}`` — the feed as the DB currently
    defines it. ``body`` is ``None`` on an unreachable worker OR a non-2xx
    response; ``status_code`` tells the caller which."""
    try:
        import httpx

        from poindexter.services.bootstrap_defaults import DEFAULT_WORKER_API_URL

        api_base = site_config.get("internal_api_base_url", DEFAULT_WORKER_API_URL)
        async with httpx.AsyncClient(
            timeout=httpx.Timeout(30.0, connect=5.0)
        ) as client:
            feed = await client.get(f"{api_base}{route}", timeout=30)
        if not (200 <= feed.status_code < 300):
            logger.warning(
                "[MEDIA_FEED_REBUILD] %s returned HTTP %d instead of a "
                "render — refusing to treat the body as feed content: %s",
                route, feed.status_code, feed.text[:300],
            )
            return _FeedFetch(body=None, status_code=feed.status_code)
        return _FeedFetch(body=feed.text, status_code=feed.status_code)
    except Exception as exc:  # noqa: BLE001 — caller decides; never raise upward
        logger.warning(
            "[MEDIA_FEED_REBUILD] could not render %s: %s", route, exc,
        )
        return _FeedFetch(body=None, status_code=None)


async def _upload_feed(
    site_config: Any, body: str, *, r2_path: str, label: str,
) -> bool:
    """Write ``body`` to a temp file and upload it to R2 ``r2_path``."""
    try:
        from poindexter.services.r2_upload_service import R2UploadService

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
        from poindexter.services.r2_upload_service import R2UploadService

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

    Non-fatal: any failure (worker unreachable, non-2xx route, R2
    unconfigured, upload error) is logged and swallowed — the rebuild is
    additive self-healing, never part of the approval transaction. Unlike
    :func:`reconcile_feed`, a non-2xx response here is not escalated with a
    finding of its own; the reconciler is the backstop that notices and
    reports a route that stays broken.
    """
    fetch = await _fetch_rendered_feed(site_config, route)
    if fetch.body is None:
        return
    await _upload_feed(site_config, fetch.body, r2_path=r2_path, label=label)


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
    rebuild was missed. Three refusals keep the loop safe:

    - a render we couldn't obtain at all (worker unreachable) is never
      published — there's nothing to converge on, and this does NOT emit a
      finding: a briefly unreachable worker isn't itself an operator-actionable
      signal, and the next cycle tries again;
    - a render whose route answered but with a non-2xx status is never
      published either. Unlike the unreachable case, this DOES escalate — the
      route itself is broken (``storage_public_url``/``site_url`` unset, or an
      unhandled error), which is strictly more actionable than "nothing
      changed" — reported through the same finding a collapsed render uses
      (``media_feed_render_collapse``);
    - a render that would shrink the feed past ``media_feed_reconcile_max_shrink``
      is never published, because the likeliest cause is a broken renderer (the
      feed route returns a valid *empty* feed when its DB query fails), not 90
      episodes genuinely disappearing. Also escalated as
      ``media_feed_render_collapse``.

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
        fetch = await _fetch_rendered_feed(site_config, spec.route)

        if fetch.status_code is not None and fetch.body is None:
            published = await _read_published_feed(site_config, spec.r2_path)
            published_items = count_feed_items(published)
            logger.error(
                "[MEDIA_FEED_RECONCILE] %s route returned HTTP %d instead of "
                "a render — refusing to publish; %d item(s) remain published.",
                spec.label, fetch.status_code, published_items,
            )
            return FeedReconcileResult(
                medium=medium, rendered_items=0, published_items=published_items,
                drifted=False, healed=False, refused=True,
                status_code=fetch.status_code,
                error=(
                    f"feed route returned HTTP {fetch.status_code} instead of "
                    f"a render — refused to publish"
                ),
            )

        if fetch.body is None:
            return FeedReconcileResult(
                medium=medium, rendered_items=0, published_items=0,
                drifted=False, healed=False,
                error="feed route unreachable — nothing safe to publish",
            )
        rendered = fetch.body

        published = await _read_published_feed(site_config, spec.r2_path)
        rendered_items = count_feed_items(rendered)
        published_items = count_feed_items(published)

        if published is not None and (
            _normalize_for_compare(rendered) == _normalize_for_compare(published)
        ):
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
