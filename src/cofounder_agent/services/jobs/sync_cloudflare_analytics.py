"""SyncCloudflareAnalyticsJob — pull page-view rows from CF Analytics Engine.

Closes the 50-day gap (page_views beacon silent since 2026-04-09). The
Cloudflare Worker at ``infrastructure/cloudflare/page-views-beacon/``
writes one data point per view to the ``analytics_events`` Analytics Engine
dataset; this job pulls those rows out via the CF AE SQL HTTP API every
5 minutes and inserts them into the existing ``page_views`` table.

Downstream consumers (Grafana panels, ``posts.view_count``,
``lab_outcomes_v1.views_*_post_publish`` columns) all keep reading from
``page_views`` unchanged — this job is purely the ingest side.

Config (``plugin.job.sync_cloudflare_analytics``):
- ``enabled`` (default ``true``)
- ``interval_seconds`` (default 300)
- ``config.batch_size`` (default 5000) — rows per fetch
- ``config.lookback_hours`` (default 24) — first-run window (only used
  when ``cloudflare_analytics_last_sync`` is unset / 1970)

Settings (``app_settings``, read via the secret-aware SiteConfig seam):
- ``cloudflare_account_id`` — non-secret, ID for the CF account that
  owns the dataset.
- ``cloudflare_analytics_api_token`` — secret, scoped to
  ``Account → Account Analytics → Read``. Read via ``get_secret()``
  per ``feedback_module_singleton_gotcha`` / ``feedback_db_first_config``.
- ``cloudflare_analytics_last_sync`` — non-secret high-water mark
  (ISO-8601 UTC). Seeded to ``1970-01-01T00:00:00Z`` so the first run
  pulls the configured lookback window.
"""

from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone
from typing import Any

from plugins.job import JobResult
from utils.findings import emit_finding

logger = logging.getLogger(__name__)


_SQL_API_URL = "https://api.cloudflare.com/client/v4/accounts/{account_id}/analytics_engine/sql"

# AE SQL — selects the columns our page_views table expects. blob4
# (country) is ignored on insert because page_views has no column for
# it yet; if a future lab phase wants geo, add the column + select
# blob4 here. Using a parameterised since-clause to keep the lookback
# bounded by our high-water mark.
_QUERY_TEMPLATE = (
    "SELECT "
    "blob1 AS slug, "
    "blob2 AS path, "
    "blob3 AS referrer, "
    "blob5 AS user_agent, "
    "timestamp AS created_at "
    "FROM analytics_events "
    "WHERE timestamp > toDateTime('{since}', 'UTC') "
    # ORDER BY must reference the SELECT alias (created_at), NOT the raw
    # `timestamp` column. CF Analytics Engine's SQL rejects an aliased column
    # referenced by its original name in ORDER BY with the misleading error
    # "unable to find type of column: timestamp" — which silently broke the
    # whole ingest (poindexter#555). WHERE/SELECT on `timestamp` are fine; only
    # ORDER BY needs the alias.
    "ORDER BY created_at ASC "
    "LIMIT {limit} "
    "FORMAT JSON"
)


def _parse_iso(value: str) -> datetime:
    """Parse ISO-8601 timestamp, tolerating trailing Z."""
    if value.endswith("Z"):
        value = value[:-1] + "+00:00"
    return datetime.fromisoformat(value)


class SyncCloudflareAnalyticsJob:
    name = "sync_cloudflare_analytics"
    description = (
        "Pull page-view rows from Cloudflare Analytics Engine via the "
        "SQL HTTP API and insert them into the local page_views table."
    )
    schedule = "every 5 minutes"
    idempotent = True

    async def run(self, pool: Any, config: dict[str, Any]) -> JobResult:
        # DI seam (glad-labs-stack#330)
        sc = config.get("_site_config")
        if sc is None:
            return JobResult(
                ok=True,
                detail="no _site_config in job config — skipping",
                changes_made=0,
            )

        account_id = (sc.get("cloudflare_account_id", "") or "").strip()
        if not account_id:
            return JobResult(
                ok=True,
                detail="cloudflare_account_id unset — skipping",
                changes_made=0,
            )

        try:
            api_token = (
                await sc.get_secret("cloudflare_analytics_api_token", "")
            ).strip()
        except Exception as e:
            logger.warning(
                "[SYNC_CF_AE] get_secret(cloudflare_analytics_api_token) "
                "failed: %s",
                e,
            )
            emit_finding(
                source="sync_cloudflare_analytics",
                kind="analytics_ingest_degraded",
                severity="warn",
                title="page_views ingest degraded — CF Analytics token read failed",
                body=(
                    "Reading the secret `cloudflare_analytics_api_token` "
                    f"raised: {e}. The CF Analytics Engine → page_views "
                    "ingest cannot run until the secret is readable; "
                    "first-party page-view data is not being collected."
                ),
                dedup_key="cf_ae:get_secret_failed",
            )
            # FAIL LOUD (poindexter#555): a secret-read failure used to
            # return ok=True, masking a dead ingest as green. Surface it
            # as degraded so the job-health signal goes red.
            return JobResult(
                ok=False,
                detail=f"get_secret failed: {e}",
                changes_made=0,
            )
        if not api_token:
            # Reached only AFTER cloudflare_account_id is confirmed set
            # (see the early-return above), so this is a HALF-CONFIGURED
            # state — the operator wired CF AE but the read token is
            # missing — not a benign "CF not set up" fresh install. Fail
            # loud + emit a finding so it stops masking the outage green
            # (poindexter#555: page_views was silently dead for ~54 days).
            emit_finding(
                source="sync_cloudflare_analytics",
                kind="analytics_ingest_degraded",
                severity="warn",
                title="page_views ingest degraded — CF Analytics token unset",
                body=(
                    "`cloudflare_account_id` is configured but "
                    "`cloudflare_analytics_api_token` is empty, so the "
                    "Cloudflare Analytics Engine → page_views ingest "
                    "cannot run. First-party page-view data is NOT being "
                    "collected. Set the read token (scope "
                    "`Account → Account Analytics → Read`): "
                    "`poindexter set cloudflare_analytics_api_token <token>`."
                ),
                dedup_key="cf_ae:token_unset",
            )
            return JobResult(
                ok=False,
                detail=(
                    "cloudflare_analytics_api_token unset while "
                    "cloudflare_account_id is set — first-party ingest "
                    "DEGRADED (was masking green; poindexter#555)"
                ),
                changes_made=0,
            )

        batch_size = int(config.get("batch_size", 5000))
        lookback_hours = int(config.get("lookback_hours", 24))

        try:
            import httpx
        except ImportError:
            return JobResult(ok=False, detail="httpx not available", changes_made=0)

        # ------------------------------------------------------------------
        # Read the high-water mark + ensure the page_views table exists.
        # ------------------------------------------------------------------
        try:
            async with pool.acquire() as conn:
                await conn.execute(
                    """
                    CREATE TABLE IF NOT EXISTS page_views (
                        id SERIAL PRIMARY KEY,
                        path TEXT,
                        slug TEXT,
                        referrer TEXT,
                        user_agent TEXT,
                        created_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP
                    )
                    """
                )
                row = await conn.fetchrow(
                    "SELECT value FROM app_settings WHERE key = 'cloudflare_analytics_last_sync'"
                )
        except Exception as e:
            logger.warning("[SYNC_CF_AE] DB precheck failed: %s", e)
            return JobResult(ok=False, detail=f"db precheck failed: {e}", changes_made=0)

        last_sync_raw = (row["value"] if row and row["value"] else "").strip()
        if last_sync_raw:
            try:
                since = _parse_iso(last_sync_raw)
            except Exception:
                logger.warning(
                    "[SYNC_CF_AE] bad cloudflare_analytics_last_sync value "
                    "(%r) — falling back to lookback window",
                    last_sync_raw,
                )
                since = datetime.now(timezone.utc) - timedelta(hours=lookback_hours)
        else:
            since = datetime.now(timezone.utc) - timedelta(hours=lookback_hours)

        # CF AE wants "YYYY-MM-DD HH:MM:SS" UTC; coerce to UTC then format.
        since_utc = since.astimezone(timezone.utc)
        since_str = since_utc.strftime("%Y-%m-%d %H:%M:%S")

        url = _SQL_API_URL.format(account_id=account_id)
        sql = _QUERY_TEMPLATE.format(since=since_str, limit=batch_size)

        # ------------------------------------------------------------------
        # Query CF AE.
        # ------------------------------------------------------------------
        try:
            async with httpx.AsyncClient(timeout=30.0) as client:
                resp = await client.post(
                    url,
                    headers={
                        "Authorization": f"Bearer {api_token}",
                        "Content-Type": "text/plain",
                    },
                    content=sql,
                )
        except Exception as e:
            logger.warning("[SYNC_CF_AE] CF SQL API request failed: %s", e)
            return JobResult(ok=False, detail=f"cf api request failed: {e}", changes_made=0)

        if resp.status_code != 200:
            logger.warning(
                "[SYNC_CF_AE] CF SQL API returned %s: %s",
                resp.status_code,
                resp.text[:500],
            )
            return JobResult(
                ok=False,
                detail=f"cf api returned {resp.status_code}",
                changes_made=0,
            )

        try:
            payload = resp.json()
        except Exception as e:
            logger.warning("[SYNC_CF_AE] CF SQL API JSON decode failed: %s", e)
            return JobResult(ok=False, detail=f"cf api json failed: {e}", changes_made=0)

        # CF AE SQL HTTP API returns ``{"meta": [...], "data": [...rows], ...}``
        # with each row as ``{"slug": "...", "path": "...", "created_at": "..."}``.
        rows = payload.get("data") or []

        if not rows:
            # No rows — advance high-water mark to "now" so the next pull
            # bounds itself sensibly even if traffic is sparse. Empty
            # response is the happy-path no-op, not a failure.
            new_high_water = datetime.now(timezone.utc).isoformat()
            await self._update_high_water_mark(pool, new_high_water)
            return JobResult(ok=True, detail="no new rows", changes_made=0)

        # ------------------------------------------------------------------
        # Insert + bump posts.view_count + advance high-water mark, in one
        # transaction so the watermark only moves on full success.
        # ------------------------------------------------------------------
        try:
            inserted = 0
            seen_slugs: dict[str, int] = {}
            max_ts: datetime | None = None

            async with pool.acquire() as conn:
                async with conn.transaction():
                    for raw in rows:
                        slug = (raw.get("slug") or "")[:500]
                        path = (raw.get("path") or "")[:500]
                        referrer = (raw.get("referrer") or "")[:1000]
                        ua = (raw.get("user_agent") or "")[:500]
                        ts_raw = raw.get("created_at") or ""
                        if not (slug or path):
                            continue
                        try:
                            # CF AE returns "YYYY-MM-DD HH:MM:SS" UTC
                            ts = datetime.strptime(
                                ts_raw, "%Y-%m-%d %H:%M:%S"
                            ).replace(tzinfo=timezone.utc)
                        except Exception:
                            # Fallback: best-effort ISO parse
                            try:
                                ts = _parse_iso(ts_raw)
                            except Exception:
                                logger.debug(
                                    "[SYNC_CF_AE] skipping row with bad timestamp %r",
                                    ts_raw,
                                )
                                continue

                        # Dedup at the row level: same (slug, path, created_at, ua)
                        # already in page_views means CF AE replayed a row across
                        # batches. Cheap exists-check, no schema change required.
                        already = await conn.fetchval(
                            "SELECT 1 FROM page_views "
                            "WHERE slug IS NOT DISTINCT FROM $1 "
                            "AND path IS NOT DISTINCT FROM $2 "
                            "AND created_at = $3 "
                            "AND user_agent IS NOT DISTINCT FROM $4 "
                            "LIMIT 1",
                            slug or None,
                            path or None,
                            ts,
                            ua or None,
                        )
                        if already:
                            continue

                        await conn.execute(
                            "INSERT INTO page_views "
                            "(path, slug, referrer, user_agent, created_at) "
                            "VALUES ($1, $2, $3, $4, $5)",
                            path or None,
                            slug or None,
                            referrer or None,
                            ua or None,
                            ts,
                        )
                        inserted += 1
                        if slug:
                            seen_slugs[slug] = seen_slugs.get(slug, 0) + 1
                        if max_ts is None or ts > max_ts:
                            max_ts = ts

                    # Bump posts.view_count in lockstep — same behaviour the
                    # deleted /api/track/view handler had per-call. Done in
                    # one UPDATE per slug rather than per-row to keep the
                    # transaction tight.
                    for slug, delta in seen_slugs.items():
                        await conn.execute(
                            "UPDATE posts "
                            "SET view_count = COALESCE(view_count, 0) + $2 "
                            "WHERE slug = $1",
                            slug,
                            delta,
                        )

            # Advance the high-water mark to the max timestamp we
            # observed. Done outside the transaction (app_settings is
            # operator-visible; a successful batch must not be re-pulled
            # even if the watermark write somehow rolls back).
            if max_ts is not None:
                await self._update_high_water_mark(pool, max_ts.isoformat())

            return JobResult(
                ok=True,
                detail=(
                    f"synced {inserted} new page_views "
                    f"({len(seen_slugs)} distinct slugs)"
                ),
                changes_made=inserted,
                metrics={
                    "rows_fetched": len(rows),
                    "rows_inserted": inserted,
                    "distinct_slugs": len(seen_slugs),
                },
            )
        except Exception as e:
            logger.exception("[SYNC_CF_AE] insert pass failed: %s", e)
            return JobResult(ok=False, detail=str(e), changes_made=0)

    async def _update_high_water_mark(self, pool: Any, value: str) -> None:
        """Persist the new high-water mark.

        Wrapped so insert-side exceptions don't escape the job — a stale
        watermark on the next cycle is recoverable (we'll just re-pull a
        few seconds of data and dedup at insert time).
        """
        try:
            async with pool.acquire() as conn:
                await conn.execute(
                    """
                    INSERT INTO app_settings
                        (key, value, category, description, is_active, is_secret)
                    VALUES (
                        'cloudflare_analytics_last_sync',
                        $1,
                        'cloudflare',
                        'High-water mark (ISO-8601 UTC) for the '
                        'sync_cloudflare_analytics job — advanced atomically '
                        'after each successful batch insert.',
                        true,
                        false
                    )
                    ON CONFLICT (key) DO UPDATE
                        SET value = EXCLUDED.value
                    """,
                    value,
                )
        except Exception as e:
            logger.warning(
                "[SYNC_CF_AE] failed to update high-water mark (%s): %s",
                value,
                e,
            )
