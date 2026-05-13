"""StaticExportReconciliationJob — DB ↔ R2 drift watchdog.

The public site (web/public-site/lib/posts.ts → ``fetchPostIndex``)
reads ``static/posts/index.json`` on R2 as its source of truth for the
homepage + archive listings. publish_service publishes by calling
``export_post`` which rewrites that index — but every prior version of
that call was fire-and-forget, so any cancelled asyncio task froze the
bucket. Between 2026-05-08 and 2026-05-11 four published posts never
reached the bucket because the background task never completed; the
public homepage silently lagged DB by 3 days with zero observability.

This watchdog is the safety net: every 15 minutes it compares the
count of published posts in Postgres against ``post_count`` in
``static/manifest.json``. On drift it fires a full rebuild and emits
an operator finding so the upstream regression doesn't fester.

Two failure modes are caught:
* **Count drift** — DB has N published posts, R2 manifest says M < N.
  Means at least one publish skipped the export step.
* **Latest-published staleness** — newest published_at in DB is more
  than ``stale_minutes`` newer than manifest.exported_at. Catches the
  case where post_count happens to match (a delete + a publish since
  the last R2 sync) but the index doesn't reflect the new content.

## Config (``plugin.job.static_export_reconciliation``)

- ``config.stale_minutes`` (default 30) — manifest must be no older
  than this when compared to the latest DB published_at
- ``config.r2_manifest_url`` (default ``https://pub-1432fdefa18e47ad98f213a8a2bf14d5.r2.dev/static/manifest.json``)
- ``config.alert_on_drift`` (default true) — emit a finding when drift
  is detected (in addition to running the rebuild)
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any

import httpx

from plugins.job import JobResult
from utils.findings import emit_finding

logger = logging.getLogger(__name__)


_MANIFEST_PATH_SUFFIX = "/static/manifest.json"


async def _resolve_manifest_url(pool: Any, config: dict[str, Any]) -> str | None:
    """Resolve the manifest URL from job config or app_settings.r2_public_url.

    Returns None when neither source is configured — caller treats that
    as "fork hasn't set up R2 yet, skip the job rather than crash".

    2026-05-12 cleanup (poindexter#485): the old ``_DEFAULT_MANIFEST_URL``
    constant baked Matt's R2 bucket name into a public OSS file. Forks
    would have pointed reconciliation at his bucket and seen drift on
    every cycle.
    """
    explicit = (config.get("r2_manifest_url") or "").strip()
    if explicit:
        return explicit
    try:
        async with pool.acquire() as conn:
            row = await conn.fetchrow(
                "SELECT value FROM app_settings WHERE key = 'r2_public_url'",
            )
        base = ((row["value"] if row else "") or "").strip().rstrip("/")
        if base:
            return f"{base}{_MANIFEST_PATH_SUFFIX}"
    except Exception as e:  # noqa: BLE001
        logger.warning(
            "static_export_reconciliation: r2_public_url lookup failed: %s", e,
        )
    return None


class StaticExportReconciliationJob:
    name = "static_export_reconciliation"
    description = "Reconcile R2 static index against Postgres; rebuild on drift"
    schedule = "every 15 minutes"
    idempotent = True  # Rebuild is itself idempotent

    async def run(self, pool: Any, config: dict[str, Any]) -> JobResult:
        stale_minutes = int(config.get("stale_minutes", 30))
        manifest_url = await _resolve_manifest_url(pool, config)
        if not manifest_url:
            logger.warning(
                "static_export_reconciliation: skipped — no manifest URL "
                "resolved (set app_settings.r2_public_url or "
                "config.r2_manifest_url)",
            )
            try:
                emit_finding(
                    source="static_export_reconciliation",
                    kind="manifest_url_unresolved",
                    severity="info",
                    title="Static-export reconciliation skipped — R2 not configured",
                    body=(
                        "Neither config.r2_manifest_url nor "
                        "app_settings.r2_public_url is set. Static-export "
                        "drift detection is dormant until one of them is."
                    ),
                    dedup_key="static_export_manifest_url_unresolved",
                )
            except Exception:
                # poindexter#455 — symmetric fix with media_reconciliation.
                # emit_finding is the operator-visible signal for
                # "this job is dormant"; debug-log on failure since the
                # warning above already covers the log channel.
                logger.debug(
                    "[static_export_reconciliation] emit_finding for "
                    "manifest_url_unresolved raised — operator visibility "
                    "degrades to log channel only",
                    exc_info=True,
                )
            return JobResult(
                ok=True,
                detail="skipped — no R2 manifest URL configured",
            )
        alert_on_drift = bool(config.get("alert_on_drift", True))

        try:
            async with pool.acquire() as conn:
                row = await conn.fetchrow(
                    """
                    SELECT COUNT(*)         AS db_count,
                           MAX(published_at) AS db_latest
                      FROM posts
                     WHERE status = 'published'
                       AND (published_at IS NULL OR published_at <= NOW())
                    """,
                )
        except Exception as e:
            logger.exception("static_export_reconciliation: DB query failed: %s", e)
            return JobResult(
                ok=False, detail=f"DB query failed: {e}", changes_made=0,
            )

        db_count = int(row["db_count"] or 0)
        db_latest = row["db_latest"]

        try:
            async with httpx.AsyncClient(
                timeout=httpx.Timeout(15.0, connect=5.0),
            ) as client:
                resp = await client.get(manifest_url)
                resp.raise_for_status()
                manifest = resp.json()
        except Exception as e:
            logger.warning(
                "static_export_reconciliation: manifest fetch failed (%s) — "
                "treating as drift, will rebuild",
                e,
            )
            manifest = None

        r2_count: int | None
        r2_exported_at: datetime | None
        drift_reason: str | None = None

        if manifest is None:
            r2_count = None
            r2_exported_at = None
            drift_reason = "manifest fetch failed"
        else:
            r2_count = int(manifest.get("post_count") or 0)
            r2_exported_at_raw = manifest.get("exported_at") or ""
            try:
                r2_exported_at = datetime.fromisoformat(
                    r2_exported_at_raw.replace("Z", "+00:00"),
                )
                if r2_exported_at.tzinfo is None:
                    r2_exported_at = r2_exported_at.replace(tzinfo=timezone.utc)
            except Exception:
                r2_exported_at = None

            if r2_count != db_count:
                drift_reason = (
                    f"post_count drift: DB={db_count} vs R2={r2_count}"
                )
            elif (
                db_latest is not None
                and r2_exported_at is not None
                and db_latest.tzinfo is None
            ):
                db_latest = db_latest.replace(tzinfo=timezone.utc)

            if drift_reason is None and db_latest is not None and r2_exported_at is not None:
                # Manifest was built BEFORE the newest published post: drift.
                lag_minutes = (db_latest - r2_exported_at).total_seconds() / 60
                if lag_minutes > stale_minutes:
                    drift_reason = (
                        f"manifest stale: latest_published_at "
                        f"{db_latest.isoformat()} is {lag_minutes:.0f}m newer "
                        f"than exported_at {r2_exported_at.isoformat()}"
                    )

        if drift_reason is None:
            return JobResult(
                ok=True,
                detail=f"in sync — DB count={db_count}, R2 count={r2_count}",
                changes_made=0,
                metrics={
                    "db_count": db_count,
                    "r2_count": r2_count or 0,
                    "drift": 0,
                },
            )

        logger.warning(
            "static_export_reconciliation: drift detected — %s; triggering "
            "full rebuild",
            drift_reason,
        )

        try:
            from services.static_export_service import export_full_rebuild

            rebuild_result = await export_full_rebuild(pool)
            rebuild_ok = bool(rebuild_result.get("success"))
        except Exception as e:
            logger.exception(
                "static_export_reconciliation: rebuild raised: %s", e,
            )
            rebuild_ok = False
            rebuild_result = {"success": False, "error": str(e)}

        if alert_on_drift:
            emit_finding(
                source="static_export_reconciliation",
                kind="r2_static_drift",
                severity="critical" if not rebuild_ok else "warning",
                title=(
                    "R2 static index out of sync with DB"
                    + (" (rebuild failed)" if not rebuild_ok else " (rebuilt)")
                ),
                body=(
                    f"## Drift detected\n\n{drift_reason}\n\n"
                    f"## DB state\n- published count: {db_count}\n"
                    f"- latest published_at: {db_latest}\n\n"
                    f"## R2 state\n- post_count: {r2_count}\n"
                    f"- exported_at: {r2_exported_at}\n\n"
                    f"## Rebuild outcome\n```json\n{rebuild_result}\n```"
                ),
                dedup_key="r2_static_drift",
                extra={
                    "db_count": db_count,
                    "r2_count": r2_count or 0,
                    "rebuild_ok": rebuild_ok,
                },
            )

        return JobResult(
            ok=rebuild_ok,
            detail=f"drift detected ({drift_reason}); rebuild ok={rebuild_ok}",
            changes_made=1,
            metrics={
                "db_count": db_count,
                "r2_count": r2_count or 0,
                "drift": 1,
                "rebuild_ok": int(rebuild_ok),
            },
        )
