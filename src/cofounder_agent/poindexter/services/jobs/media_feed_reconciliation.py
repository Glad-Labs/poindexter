"""MediaFeedReconciliationJob — published-feed ↔ DB drift watchdog.

Sibling to ``media_reconciliation`` (assets) and ``static_export_reconciliation``
(post JSONs). Those two converge the *files*; this one converges the *feeds*
that index them.

## The gap this closes

Every podcast/video RSS rebuild is triggered by a discrete event, and each one
swallows its own failures:

* ``media_approval_service.decide`` rebuilds on approve — only when the calling
  surface threads a ``site_config`` through, and inside a bare ``except``.
* ``podcast_distribute`` Pass 3 rebuilds — only when that cycle delivered a
  *new* approved-and-undispatched asset. An episode whose URL was stamped by
  any other path reads as already-dispatched and never re-triggers it.
* ``media_distribute`` (video) rebuilds only when that cycle dispatched a new
  video — an already-dispatched one never re-triggers it.

So a single dropped event left the published feed wrong until some unrelated
later event happened to rebuild it. Measured on 2026-07-18: the podcast feed on
R2 listed **71** episodes while **100** were DB-eligible — a 29-episode backlog
that had survived weeks of publishes, and which only a manual
``rebuild_podcast_feed`` cleared. (A fourth trigger, ``publish_service``, used
to rebuild at publish time — before media is even approved, so it could never
contain the post it fired for. Its tail was retired 2026-09-25 as dead code:
unreachable from the default approve→stage→promote flow, 30+ days with zero
uploads.)

## What it does

Every cycle, for each medium with an RSS surface: render the feed from the DB,
read the published object out of the bucket, and republish when they differ.
The feed becomes a function of state rather than of events, so *no* future
delivery path can starve it — including ones not written yet
(``feedback_self_heal_not_suppress``).

## Fail-loud contract

Self-healing silently would hide the upstream regression, so a converged feed
still emits a ``media_feed_drift`` finding (``warn``) — the same contract
``media_reconciliation`` uses.

A *refused* reconcile emits ``media_feed_render_collapse`` at ``error``. That
one is not ordinary drift, and it covers two distinct causes, both worse than
staleness: the renderer produced far fewer episodes than are published (and
since ``podcast_feed`` returns a **valid empty feed** when its DB query
fails, publishing it would wipe the podcast off Apple and Spotify), or the
feed route answered with a non-2xx status instead of a render at all (e.g.
``storage_public_url``/``site_url`` unset) — a broken route, not a shrunk
feed, but equally something the watchdog must never publish. The watchdog
declines and escalates instead of self-harming either way;
``FeedReconcileResult.status_code`` tells the two apart for the finding body.

Enabled by default (``media_feed_reconciliation_enabled``) — unlike the Stage-2
media jobs it needs no GPU and no dormant master switch; it is a read-mostly
safety net whose steady state is one render + one GetObject per medium.
"""

from __future__ import annotations

import logging
from typing import Any

from poindexter.plugins.job import JobResult
from poindexter.services.media_feed_rebuild import RECONCILABLE_MEDIA, reconcile_feed
from poindexter.utils.exception_format import describe_exception
from poindexter.utils.findings import emit_finding

logger = logging.getLogger(__name__)


def _emit_drift_finding(medium: str, res: Any) -> None:
    """Tell the operator the feed had drifted, even though we just fixed it."""
    emit_finding(
        source="media_feed_reconciliation",
        kind="media_feed_drift",
        severity="warn",
        title=f"{medium} RSS feed had drifted from the database",
        body=(
            f"The published {medium} feed listed {res.published_items} episodes "
            f"but {res.rendered_items} are currently eligible. The feed has been "
            f"republished, so subscribers are current again.\n\n"
            f"The drift itself means an upstream rebuild was missed — the "
            f"event-coupled rebuilds live in media_approval_service.decide, "
            f"podcast_distribute and media_distribute. Worth checking which "
            f"one dropped it if this finding keeps recurring."
        ),
        dedup_key=f"media_feed_drift:{medium}",
        extra={
            "medium": medium,
            "published_items": res.published_items,
            "rendered_items": res.rendered_items,
        },
    )


def _emit_collapse_finding(medium: str, res: Any) -> None:
    """The render collapsed — publishing it would have destroyed the feed.

    Two distinct causes share this finding: a shrunk render (``res.status_code``
    is ``None``) or a route that answered non-2xx instead of rendering at all
    (``res.status_code`` set). The body branches so an operator reading it
    isn't told about a "shrink" that never happened.
    """
    if res.status_code is not None:
        title = f"{medium} feed route returned HTTP {res.status_code} — republish REFUSED"
        body = (
            f"The {medium} feed route returned HTTP {res.status_code} instead of "
            f"a rendered feed, so nothing was published. {res.published_items} "
            f"episode(s) remain live and untouched.\n\n"
            f"A non-2xx response means the route itself is broken — most likely "
            f"storage_public_url or site_url is unset, or the worker hit an "
            f"unhandled error. Check the worker's /api/{medium}/feed.xml route "
            f"directly before assuming the episodes are the problem.\n\n"
            f"Detail: {res.error}"
        )
    else:
        title = f"{medium} feed render collapsed — republish REFUSED"
        body = (
            f"Rendering the {medium} feed produced {res.rendered_items} episodes "
            f"against {res.published_items} currently published. That shrink "
            f"exceeds media_feed_reconcile_max_shrink, so the published feed was "
            f"left untouched — publishing this render would have removed "
            f"episodes from Apple/Spotify.\n\n"
            f"The feed route returns a valid *empty* feed when its database "
            f"query fails, so the likeliest cause is the renderer or the DB, not "
            f"missing episodes. Check the worker's /api/{medium}/feed.xml route "
            f"and the database before overriding the guard.\n\n"
            f"Detail: {res.error}"
        )
    emit_finding(
        source="media_feed_reconciliation",
        kind="media_feed_render_collapse",
        severity="error",
        title=title,
        body=body,
        dedup_key=f"media_feed_render_collapse:{medium}",
        extra={
            "medium": medium,
            "published_items": res.published_items,
            "rendered_items": res.rendered_items,
            "status_code": res.status_code,
        },
    )


class MediaFeedReconciliationJob:
    name = "media_feed_reconciliation"
    description = (
        "Converge the published podcast/video RSS feeds on R2 onto the "
        "database's current eligible-episode set, so a missed event-coupled "
        "rebuild can't leave subscribers on a stale feed"
    )
    schedule = "every 15 minutes"
    idempotent = True

    async def run(self, pool: Any, config: dict[str, Any]) -> JobResult:
        sc = config.get("_site_config")
        if sc is None:
            return JobResult(ok=True, detail="no site_config — skipping", changes_made=0)

        if not sc.get_bool("media_feed_reconciliation_enabled", True):
            return JobResult(
                ok=True,
                detail="media_feed_reconciliation_enabled=false — disabled",
                changes_made=0,
            )

        healed = 0
        refused = 0
        metrics: dict[str, Any] = {}
        details: list[str] = []

        for medium in RECONCILABLE_MEDIA:
            try:
                res = await reconcile_feed(sc, medium)
            except Exception as exc:  # noqa: BLE001 — one medium must not halt the pass
                logger.warning(
                    "[MEDIA_FEED_RECONCILE] %s reconcile raised: %s", medium, describe_exception(exc),
                )
                details.append(f"{medium}: error")
                continue

            metrics[f"{medium}_rendered_items"] = res.rendered_items
            metrics[f"{medium}_published_items"] = res.published_items

            if res.refused:
                refused += 1
                _emit_collapse_finding(medium, res)
                details.append(
                    f"{medium}: REFUSED (HTTP {res.status_code})"
                    if res.status_code is not None else f"{medium}: REFUSED",
                )
                continue

            if res.healed:
                healed += 1
                _emit_drift_finding(medium, res)
                details.append(
                    f"{medium}: healed {res.published_items}→{res.rendered_items}",
                )
                continue

            if res.error:
                details.append(f"{medium}: {res.error}")
                continue

            details.append(f"{medium}: in sync ({res.rendered_items})")

        metrics["feeds_healed"] = healed
        metrics["feeds_refused"] = refused

        return JobResult(
            ok=True,
            detail="; ".join(details) or "nothing to reconcile",
            changes_made=healed,
            metrics=metrics,
        )


__all__ = ["MediaFeedReconciliationJob"]
