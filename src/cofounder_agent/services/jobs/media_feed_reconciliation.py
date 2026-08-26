"""MediaFeedReconciliationJob — published-feed ↔ DB drift watchdog.

Sibling to ``media_reconciliation`` (assets) and ``static_export_reconciliation``
(post JSONs). Those two converge the *files*; this one converges the *feeds*
that index them.

## The gap this closes

Every podcast/video RSS rebuild is triggered by a discrete event, and each one
swallows its own failures:

* ``publish_service`` rebuilds at publish — but media is approved *after*
  publish, so that rebuild can never contain the post it fired for.
* ``media_approval_service.decide`` rebuilds on approve — only when the calling
  surface threads a ``site_config`` through, and inside a bare ``except``.
* ``podcast_distribute`` Pass 3 rebuilds — only when that cycle delivered a
  *new* approved-and-undispatched asset. An episode whose URL was stamped by
  any other path reads as already-dispatched and never re-triggers it.
* ``media_distribute`` (video) rebuilt nothing at all.

So a single dropped event left the published feed wrong until some unrelated
later event happened to rebuild it. Measured on 2026-07-18: the podcast feed on
R2 listed **71** episodes while **100** were DB-eligible — a 29-episode backlog
that had survived weeks of publishes, and which only a manual
``rebuild_podcast_feed`` cleared.

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
one is not ordinary drift: it means the renderer produced far fewer episodes
than are published, and since ``podcast_feed`` returns a **valid empty feed**
when its DB query fails, publishing it would wipe the podcast off Apple and
Spotify. The watchdog declines and escalates instead of self-harming.

Enabled by default (``media_feed_reconciliation_enabled``) — unlike the Stage-2
media jobs it needs no GPU and no dormant master switch; it is a read-mostly
safety net whose steady state is one render + one GetObject per medium.
"""

from __future__ import annotations

import logging
from typing import Any

from plugins.job import JobResult
from services.media_feed_rebuild import RECONCILABLE_MEDIA, reconcile_feed
from utils.exception_format import describe_exception
from utils.findings import emit_finding

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
            f"event-coupled rebuilds live in publish_service, "
            f"media_approval_service.decide, podcast_distribute and "
            f"media_distribute. Worth checking which one dropped it if this "
            f"finding keeps recurring."
        ),
        dedup_key=f"media_feed_drift:{medium}",
        extra={
            "medium": medium,
            "published_items": res.published_items,
            "rendered_items": res.rendered_items,
        },
    )


def _emit_collapse_finding(medium: str, res: Any) -> None:
    """The render collapsed — publishing it would have destroyed the feed."""
    emit_finding(
        source="media_feed_reconciliation",
        kind="media_feed_render_collapse",
        severity="error",
        title=f"{medium} feed render collapsed — republish REFUSED",
        body=(
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
        ),
        dedup_key=f"media_feed_render_collapse:{medium}",
        extra={
            "medium": medium,
            "published_items": res.published_items,
            "rendered_items": res.rendered_items,
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
                details.append(f"{medium}: REFUSED")
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
