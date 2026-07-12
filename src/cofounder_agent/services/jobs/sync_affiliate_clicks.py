"""SyncAffiliateClicksJob — pull affiliate-click rows from CF Analytics Engine.

Mirrors ``SyncCloudflareAnalyticsJob`` (``services/jobs/sync_cloudflare_analytics.py``):
the affiliate-redirect Worker (``infrastructure/cloudflare/affiliate-redirect/``)
writes one AE data point per ``/go/<code>`` click to the ``affiliate_clicks``
dataset; this job pulls them via the CF SQL HTTP API every 5 min into
``affiliate_link_clicks``, then rolls per-code counts up into
``affiliate_links.clicks``. High-water mark lives in ``app_settings``.

Config (``plugin.job.sync_affiliate_clicks``):
- ``enabled`` (default ``true``), ``interval_seconds`` (default 300)
- ``config.batch_size`` (default 5000), ``config.lookback_hours`` (default 24)

Settings (``app_settings``, reused from the page-views ingest):
- ``cloudflare_account_id`` — non-secret account ID that owns the dataset.
- ``cloudflare_analytics_api_token`` — secret, ``Account → Account Analytics → Read``.
- ``affiliate_clicks_last_sync`` — non-secret high-water mark (ISO-8601 UTC),
  upserted by this job (not seeded — the first run uses the lookback window).

Unlike the always-on page-views ingest, the affiliate feature is opt-in
(``affiliate_injection_enabled`` defaults ``false``), so a missing CF token on a
fresh install is benign — there are no ``/go`` links, hence no clicks — and the
job skips quietly rather than failing loud / emitting a finding.
"""

from __future__ import annotations

import logging
import re
from datetime import datetime, timedelta, timezone
from typing import Any

from plugins.job import JobResult
from utils.findings import emit_finding

logger = logging.getLogger(__name__)

_SQL_API_URL = "https://api.cloudflare.com/client/v4/accounts/{account_id}/analytics_engine/sql"

# AE SQL — maps the Worker's blob layout to affiliate_link_clicks columns.
# ORDER BY must reference the SELECT alias (created_at), not the raw `timestamp`
# column — CF AE rejects the latter (see poindexter#555 in the sibling job).
_QUERY_TEMPLATE = (
    "SELECT "
    "blob1 AS code, "
    "blob2 AS referrer, "
    "blob3 AS country, "
    "blob4 AS user_agent, "
    "timestamp AS created_at "
    "FROM affiliate_clicks "
    "WHERE timestamp > toDateTime('{since}', 'UTC') "
    "ORDER BY created_at ASC "
    "LIMIT {limit} "
    "FORMAT JSON"
)
_WATERMARK_KEY = "affiliate_clicks_last_sync"
_SLUG_RE = re.compile(r"/posts/([^/?#]+)")


def _parse_iso(value: str) -> datetime:
    """Parse ISO-8601 timestamp, tolerating a trailing Z."""
    if value.endswith("Z"):
        value = value[:-1] + "+00:00"
    return datetime.fromisoformat(value)


def _row_to_click(raw: dict[str, Any]) -> dict[str, Any] | None:
    """Map one AE row to an affiliate_link_clicks insert dict, or None to skip.

    ``post_slug`` is derived from the referrer's ``/posts/<slug>`` path so the
    click can be attributed to the source post without the Worker knowing slugs.
    A row with no code is dropped (nothing to attribute the click to).
    """
    code = (raw.get("code") or "").strip()
    if not code:
        return None
    referrer = (raw.get("referrer") or "")[:1000]
    m = _SLUG_RE.search(referrer)
    return {
        "code": code[:64],
        "post_slug": (m.group(1) if m else None),
        "referrer": referrer or None,
        "country": (raw.get("country") or "")[:8] or None,
        "user_agent": (raw.get("user_agent") or "")[:500] or None,
        "created_at_raw": raw.get("created_at") or "",
    }


def _emit_bad_timestamp_finding(skipped: int) -> None:
    """Emit ONE aggregate finding when a batch skipped affiliate-click rows with
    unparseable timestamps. No-op when nothing was skipped."""
    if not skipped:
        return
    emit_finding(
        source="sync_affiliate_clicks",
        kind="affiliate_click_parse_skipped",
        title=f"{skipped} affiliate-click row(s) skipped — unparseable timestamp",
        body=(
            f"sync_affiliate_clicks skipped {skipped} row(s) this batch because "
            "created_at did not match the expected format. A systematic skip means "
            "the click-beacon timestamp format drifted and click counts are "
            "under-reporting — check the CF AE SQL response shape."
        ),
        severity="info",
        dedup_key="affiliate_click_parse_skipped",
        extra={"skipped": skipped},
    )


class SyncAffiliateClicksJob:
    name = "sync_affiliate_clicks"
    description = (
        "Pull affiliate /go clicks from Cloudflare Analytics Engine via the "
        "SQL HTTP API into affiliate_link_clicks, then roll up per-code totals."
    )
    schedule = "every 5 minutes"
    idempotent = True

    async def run(self, pool: Any, config: dict[str, Any]) -> JobResult:
        # DI seam (glad-labs-stack#330)
        sc = config.get("_site_config")
        if sc is None:
            return JobResult(
                ok=True, detail="no _site_config in job config — skipping", changes_made=0
            )

        account_id = (sc.get("cloudflare_account_id", "") or "").strip()
        if not account_id:
            return JobResult(
                ok=True, detail="cloudflare_account_id unset — skipping", changes_made=0
            )

        try:
            api_token = (await sc.get_secret("cloudflare_analytics_api_token", "")).strip()
        except Exception as e:
            logger.warning("[SYNC_AFFILIATE] get_secret failed: %s", e)
            return JobResult(ok=False, detail=f"get_secret failed: {e}", changes_made=0)
        if not api_token:
            # Opt-in feature: a missing token on a fresh install is benign (no
            # /go links exist → no clicks). Skip quietly — do NOT fail loud or
            # emit a finding the way the always-on page-views ingest does.
            return JobResult(
                ok=True,
                detail="cloudflare_analytics_api_token unset — skipping",
                changes_made=0,
            )

        batch_size = int(config.get("batch_size", 5000))
        lookback_hours = int(config.get("lookback_hours", 24))

        try:
            import httpx
        except ImportError:
            return JobResult(ok=False, detail="httpx not available", changes_made=0)

        # ------------------------------------------------------------------
        # Read the high-water mark.
        # ------------------------------------------------------------------
        try:
            async with pool.acquire() as conn:
                row = await conn.fetchrow(
                    "SELECT value FROM app_settings WHERE key = $1", _WATERMARK_KEY
                )
        except Exception as e:
            logger.warning("[SYNC_AFFILIATE] watermark read failed: %s", e)
            return JobResult(ok=False, detail=f"watermark read failed: {e}", changes_made=0)

        last = (row["value"] if row and row["value"] else "").strip()
        since: datetime | None = None
        if last:
            try:
                since = _parse_iso(last)
            except Exception:
                logger.warning(
                    "[SYNC_AFFILIATE] bad %s value (%r) — using lookback window",
                    _WATERMARK_KEY,
                    last,
                )
        if since is None:
            since = datetime.now(timezone.utc) - timedelta(hours=lookback_hours)
        since_str = since.astimezone(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")

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
            logger.warning("[SYNC_AFFILIATE] CF SQL API request failed: %s", e)
            return JobResult(ok=False, detail=f"cf api request failed: {e}", changes_made=0)

        if resp.status_code != 200:
            logger.warning(
                "[SYNC_AFFILIATE] CF SQL API returned %s: %s",
                resp.status_code,
                resp.text[:500],
            )
            return JobResult(
                ok=False, detail=f"cf api returned {resp.status_code}", changes_made=0
            )

        try:
            payload = resp.json()
        except Exception as e:
            logger.warning("[SYNC_AFFILIATE] CF SQL API JSON decode failed: %s", e)
            return JobResult(ok=False, detail=f"cf api json failed: {e}", changes_made=0)

        rows = payload.get("data") or []
        if not rows:
            # Advance the watermark to now so a quiet period doesn't force an
            # ever-growing rescan window each cycle (matches the sibling ingest).
            await self._update_high_water_mark(pool, datetime.now(timezone.utc).isoformat())
            return JobResult(ok=True, detail="no new rows", changes_made=0)

        # ------------------------------------------------------------------
        # Insert (dedup) + rollup + advance watermark to the max ts observed.
        # ------------------------------------------------------------------
        try:
            inserted = 0
            skipped_bad_ts = 0
            max_ts: datetime | None = None
            async with pool.acquire() as conn:
                async with conn.transaction():
                    for raw in rows:
                        c = _row_to_click(raw)
                        if c is None:
                            continue
                        try:
                            ts = datetime.strptime(
                                c["created_at_raw"], "%Y-%m-%d %H:%M:%S"
                            ).replace(tzinfo=timezone.utc)
                        except Exception:
                            try:
                                ts = _parse_iso(c["created_at_raw"])
                            except Exception:
                                skipped_bad_ts += 1
                                logger.debug(
                                    "[SYNC_AFFILIATE] skipping row with bad timestamp %r",
                                    c["created_at_raw"],
                                )
                                continue

                        # Dedup at the row level — CF AE can replay a row across
                        # batches. (code, created_at, user_agent) is enough to
                        # identify a replay without a schema change.
                        dup = await conn.fetchval(
                            "SELECT 1 FROM affiliate_link_clicks "
                            "WHERE code = $1 AND created_at = $2 "
                            "AND user_agent IS NOT DISTINCT FROM $3 LIMIT 1",
                            c["code"],
                            ts,
                            c["user_agent"],
                        )
                        if dup:
                            continue

                        await conn.execute(
                            "INSERT INTO affiliate_link_clicks "
                            "(code, post_slug, referrer, country, user_agent, created_at) "
                            "VALUES ($1, $2, $3, $4, $5, $6)",
                            c["code"],
                            c["post_slug"],
                            c["referrer"],
                            c["country"],
                            c["user_agent"],
                            ts,
                        )
                        inserted += 1
                        if max_ts is None or ts > max_ts:
                            max_ts = ts

            # Roll per-code counts into affiliate_links.clicks (deterministic;
            # safe to re-run — it recomputes from affiliate_link_clicks). SQL
            # against the shared tables, never a modules.* import — the kernel
            # job talks to the DB (spinal cord), not module Python (#666).
            if inserted:
                await self._rollup_clicks(pool)
            if max_ts is not None:
                await self._update_high_water_mark(pool, max_ts.isoformat())

            _emit_bad_timestamp_finding(skipped_bad_ts)

            return JobResult(
                ok=True,
                detail=f"synced {inserted} affiliate clicks",
                changes_made=inserted,
                metrics={"rows_fetched": len(rows), "rows_inserted": inserted},
            )
        except Exception as e:
            logger.exception("[SYNC_AFFILIATE] insert pass failed: %s", e)
            return JobResult(ok=False, detail=str(e), changes_made=0)

    async def _rollup_clicks(self, pool: Any) -> None:
        """Recompute affiliate_links.clicks from affiliate_link_clicks.

        SQL against the shared tables (spinal cord) — the kernel job must not
        import the content module (kernel_purity_lint / poindexter#666). Best
        effort: a rollup hiccup must not fail an ingest that already inserted.
        """
        try:
            async with pool.acquire() as conn:
                await conn.execute(
                    "UPDATE affiliate_links a SET clicks = c.n, updated_at = now() "
                    "FROM (SELECT code, COUNT(*) AS n FROM affiliate_link_clicks GROUP BY code) c "
                    "WHERE a.code = c.code AND a.clicks <> c.n"
                )
        except Exception as e:  # noqa: BLE001 — rollup is best-effort
            logger.warning("[SYNC_AFFILIATE] click rollup failed: %s", e)

    async def _update_high_water_mark(self, pool: Any, value: str) -> None:
        """Persist the new high-water mark. Wrapped so a write hiccup doesn't
        escape the job — a stale watermark just re-pulls a few seconds of data,
        which the insert-time dedup absorbs."""
        try:
            async with pool.acquire() as conn:
                await conn.execute(
                    """
                    INSERT INTO app_settings
                        (key, value, category, description, is_active, is_secret)
                    VALUES (
                        $1, $2, 'cloudflare',
                        'High-water mark (ISO-8601 UTC) for the '
                        'sync_affiliate_clicks job — advanced after each batch.',
                        true, false
                    )
                    ON CONFLICT (key) DO UPDATE SET value = EXCLUDED.value
                    """,
                    _WATERMARK_KEY,
                    value,
                )
        except Exception as e:
            logger.warning(
                "[SYNC_AFFILIATE] failed to update high-water mark (%s): %s", value, e
            )


__all__ = ["SyncAffiliateClicksJob", "_row_to_click"]
