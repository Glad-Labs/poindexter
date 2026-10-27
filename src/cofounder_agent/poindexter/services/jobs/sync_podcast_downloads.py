"""SyncPodcastDownloadsJob — count podcast episode downloads from R2 analytics.

Until 2026-10-27 nothing measured whether anyone listened: 104 episodes had
been delivered and no table held a single play. The episodes are served
straight from the R2 bucket's public URL (the feed's ``<enclosure>`` points at
``{storage_public_url}/podcast/v2/<post_id>.mp3``), so the only first-party
record of a listen is an R2 ``GetObject`` on that key. Cloudflare's GraphQL
Analytics API exposes those per object and per hour
(``r2OperationsAdaptiveGroups``), readable with the same
``Account Analytics: Read`` token page views already use.

Most fetches are not listeners. In the 30 days to 2026-10-27 the 95 MP3
fetches were mostly machines: one crawl took 19 episodes in a single hour
(53 fetches, 2026-09-28), and every Monday around 17:00-19:00 UTC something
fetched 3-5 episodes from several regions at once. Only 15 hours, 18
fetches, touched a single episode. So each hour's fetches are classified in
this order:

- ``crawl_requests`` — the hour fetched ``podcast_downloads_sweep_min_episodes``
  (3) or more different episodes. A directory or crawler sweeping the catalog.
- ``ingest_requests`` — within ``podcast_downloads_ingest_window_hours`` of the
  episode reaching the feed (``media_approvals.dispatched_at``), or with no
  delivery stamp. Platforms picking up a new episode.
- ``listen_requests`` — everything else. The closest thing to a listener, and
  still an upper bound (a lone crawler fetch also lands here).

Two blind spots, both structural:

- **Spotify** downloads each episode once and serves its own copy, so Spotify
  plays never reach R2. They live only in Spotify for Podcasters.
- A podcast app may fetch one episode in several ranged requests, so
  requests overcount listens; ``bytes`` / file size is the steadier figure.

Rows land in ``external_metrics`` (``source='podcast_downloads'``), one per
episode per day per metric (``requests``, ``bytes``, ``crawl_requests``,
``ingest_requests``, ``listen_requests``), upserted on the natural key so the re-read window
converges as Cloudflare finishes a day's counts. The first run (no rows yet)
reads ``podcast_downloads_initial_lookback_days``; later runs re-read
``podcast_downloads_lookback_days``.
"""

from __future__ import annotations

import json
import logging
import re
from collections import defaultdict
from datetime import UTC, date, datetime, timedelta
from typing import Any

import httpx

from poindexter.plugins.job import JobResult
from poindexter.utils.exception_format import describe_exception

logger = logging.getLogger(__name__)

SOURCE = "podcast_downloads"
GRAPHQL_URL = "https://api.cloudflare.com/client/v4/graphql"

# podcast/v2/<post uuid>.mp3 — the key media/podcast delivery writes.
_EPISODE_KEY = re.compile(r"^podcast/(?:v\d+/)?([0-9a-f-]{36})\.mp3$")

_QUERY = """
query($acct: String!, $bucket: String!, $since: Time!, $until: Time!) {
  viewer { accounts(filter: {accountTag: $acct}) {
    r2OperationsAdaptiveGroups(limit: 10000, filter: {
        datetime_geq: $since, datetime_lt: $until, bucketName: $bucket,
        actionType: "GetObject", actionStatus: "success",
        objectName_like: "podcast/%.mp3"}) {
      sum { requests responseObjectSize }
      dimensions { objectName datetimeHour }
    }
  } }
}
"""

_DELIVERED_SQL = """
    SELECT subject_id::text AS post_id, dispatched_at
      FROM media_approvals
     WHERE medium = 'podcast' AND subject_id::text = ANY($1::text[])
"""

_SLUGS_SQL = "SELECT id::text AS post_id, slug FROM posts WHERE id::text = ANY($1::text[])"

_HAS_ROWS_SQL = "SELECT EXISTS (SELECT 1 FROM external_metrics WHERE source = $1)"

_UPSERT_SQL = """
    INSERT INTO external_metrics
      (source, metric_name, metric_value, dimensions, post_id, slug, date, fetched_at)
    VALUES ($1, $2, $3, '{}'::jsonb, $4::uuid, $5, $6, now())
    ON CONFLICT (source, metric_name, date, slug, dimensions)
    DO UPDATE SET metric_value = EXCLUDED.metric_value,
                  post_id = EXCLUDED.post_id,
                  fetched_at = EXCLUDED.fetched_at
"""


def _int(sc: Any, key: str, default: int) -> int:
    try:
        return int(sc.get(key, default))
    except (TypeError, ValueError):
        return default


def _parse_hour(value: str) -> datetime:
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


def tally(
    groups: list[dict[str, Any]],
    delivered: dict[str, datetime | None],
    ingest_window: timedelta,
    sweep_min_episodes: int = 3,
) -> dict[tuple[str, date], dict[str, float]]:
    """Sum hourly R2 groups into per-episode, per-day metrics.

    Each hour's fetches are a ``crawl`` when that hour touched
    ``sweep_min_episodes`` or more different episodes; otherwise ``ingest``
    when they land before the episode's delivery plus ``ingest_window`` (or
    delivery was never stamped: unknown is not claimed as a listen);
    otherwise ``listen``. Keys that are not episode MP3s are ignored.
    """
    hits: list[tuple[str, datetime, float, float]] = []
    episodes_per_hour: dict[datetime, set[str]] = defaultdict(set)
    for group in groups:
        dims = group.get("dimensions") or {}
        match = _EPISODE_KEY.match(str(dims.get("objectName") or ""))
        if not match:
            continue
        hour = _parse_hour(str(dims["datetimeHour"]))
        totals = group.get("sum") or {}
        hits.append((match.group(1), hour, float(totals.get("requests") or 0),
                     float(totals.get("responseObjectSize") or 0)))
        episodes_per_hour[hour].add(match.group(1))

    out: dict[tuple[str, date], dict[str, float]] = defaultdict(lambda: {
        "requests": 0.0, "bytes": 0.0, "crawl_requests": 0.0,
        "ingest_requests": 0.0, "listen_requests": 0.0,
    })
    for post_id, hour, requests, size in hits:
        row = out[(post_id, hour.date())]
        row["requests"] += requests
        row["bytes"] += size
        at = delivered.get(post_id)
        if len(episodes_per_hour[hour]) >= max(2, sweep_min_episodes):
            row["crawl_requests"] += requests
        elif at is None or hour < at + ingest_window:
            row["ingest_requests"] += requests
        else:
            row["listen_requests"] += requests
    return dict(out)


class SyncPodcastDownloadsJob:
    name = "sync_podcast_downloads"
    description = (
        "Count podcast episode downloads from Cloudflare R2 analytics into "
        "external_metrics, split into platform ingestion and later listens."
    )
    schedule = "every 6 hours"
    idempotent = True

    async def run(self, pool: Any, config: dict[str, Any]) -> JobResult:
        sc = config.get("_site_config")
        if sc is None:
            return JobResult(ok=True, detail="no _site_config in job config — skipping", changes_made=0)
        if str(sc.get("podcast_downloads_sync_enabled", "true")).strip().lower() not in ("true", "1", "yes", "on"):
            return JobResult(ok=True, detail="podcast_downloads_sync_enabled is off", changes_made=0)

        account_id = (sc.get("cloudflare_account_id", "") or "").strip()
        bucket = (sc.get("storage_bucket", "") or "").strip()
        if not account_id or not bucket:
            return JobResult(ok=True, detail="cloudflare_account_id or storage_bucket unset — skipping",
                             changes_made=0)
        try:
            token = (await sc.get_secret("cloudflare_analytics_api_token", "")).strip()
        except Exception as exc:  # noqa: BLE001 — a dead secret read is a failed run, not a skip
            return JobResult(ok=False, detail=f"get_secret failed: {describe_exception(exc)}", changes_made=0)
        if not token:
            return JobResult(ok=False, detail="cloudflare_analytics_api_token unset", changes_made=0)

        async with pool.acquire() as conn:
            has_rows = bool(await conn.fetchval(_HAS_ROWS_SQL, SOURCE))
        days = _int(sc, "podcast_downloads_lookback_days", 3) if has_rows else _int(
            sc, "podcast_downloads_initial_lookback_days", 30,
        )
        until = datetime.now(UTC).replace(minute=0, second=0, microsecond=0)
        since = until - timedelta(days=max(1, days))

        try:
            async with httpx.AsyncClient(timeout=30) as client:
                resp = await client.post(
                    GRAPHQL_URL,
                    headers={"Authorization": f"Bearer {token}"},
                    json={"query": _QUERY, "variables": {
                        "acct": account_id, "bucket": bucket,
                        "since": since.isoformat().replace("+00:00", "Z"),
                        "until": until.isoformat().replace("+00:00", "Z"),
                    }},
                )
                resp.raise_for_status()
                body = resp.json()
        except Exception as exc:  # noqa: BLE001 — reported as a failed run; the next run re-reads the window
            return JobResult(ok=False, detail=f"R2 analytics query failed: {describe_exception(exc)}",
                             changes_made=0)
        if body.get("errors"):
            return JobResult(ok=False, detail=f"R2 analytics errors: {json.dumps(body['errors'])[:300]}",
                             changes_made=0)
        accounts = ((body.get("data") or {}).get("viewer") or {}).get("accounts") or []
        groups = (accounts[0] if accounts else {}).get("r2OperationsAdaptiveGroups") or []

        post_ids = sorted({
            m.group(1) for g in groups
            if (m := _EPISODE_KEY.match(str((g.get("dimensions") or {}).get("objectName") or "")))
        })
        async with pool.acquire() as conn:
            delivered = {r["post_id"]: r["dispatched_at"] for r in await conn.fetch(_DELIVERED_SQL, post_ids)}
            slugs = {r["post_id"]: r["slug"] for r in await conn.fetch(_SLUGS_SQL, post_ids)}
        window = timedelta(hours=_int(sc, "podcast_downloads_ingest_window_hours", 6))
        totals = tally(groups, delivered, window, _int(sc, "podcast_downloads_sweep_min_episodes", 3))

        written = 0
        async with pool.acquire() as conn:
            for (post_id, day), metrics in totals.items():
                # Episodes whose post was deleted keep their counts, keyed by id.
                slug = slugs.get(post_id) or post_id
                post_ref = post_id if post_id in slugs else None
                for metric_name, value in metrics.items():
                    await conn.execute(_UPSERT_SQL, SOURCE, metric_name, value, post_ref, slug, day)
                    written += 1

        listens = sum(m["listen_requests"] for m in totals.values())
        requests = sum(m["requests"] for m in totals.values())
        detail = (
            f"{len(post_ids)} episode(s) fetched in the last {days} day(s): "
            f"{int(requests)} request(s), {int(listens)} not crawl or ingest"
        )
        logger.info("[podcast_downloads] %s", detail)
        return JobResult(
            ok=True, detail=detail, changes_made=written,
            metrics={"episodes": len(post_ids), "requests": int(requests), "listen_requests": int(listens)},
        )


__all__ = ["SyncPodcastDownloadsJob", "tally"]
