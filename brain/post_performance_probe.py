"""
Post Performance Probe — surfaces broken, fading, and performing posts.

Runs on the brain daemon cycle (Glad-Labs/poindexter#520 Stage 3+4).

Reads the latest post_performance snapshot per published post and
classifies each into one of three signal buckets:

  broken    — views_30d = 0 AND published more than 30 days ago.
              These posts exist but attract zero traffic; likely
              indexing, redirect, or quality issues.

  fading    — views_7d < (views_30d / 4) * fading_threshold_ratio.
              Weekly pace is well below the monthly baseline, implying
              declining interest. Only fires when views_30d > 0.

  performing — views_1d > performing_spike_multiplier * (views_7d / 7).
              Today's views are significantly above the 7d daily average,
              suggesting a traffic spike worth knowing about.

Alert routing:
  - broken posts  → notify_fn (Telegram) — high-priority; needs investigation.
  - fading posts  → debug log only; informational (not yet page-worthy).
  - performing    → debug log only; good news, no page needed.

App settings keys (all optional, fall back to listed defaults):

  post_performance_probe_enabled         (default "true")
  post_performance_probe_interval_minutes (default "1440" = 24h)
  post_performance_broken_min_age_days   (default "30")
  post_performance_fading_threshold_ratio (default "0.5")
  post_performance_performing_spike_multiplier (default "3.0")
"""

import logging
import time
from typing import Any

logger = logging.getLogger("brain.post_performance_probe")

_last_run: dict[str, float] = {}


def _is_due(probe_name: str, interval_minutes: int) -> bool:
    last = _last_run.get(probe_name, 0)
    return (time.time() - last) >= interval_minutes * 60


def _mark_run(probe_name: str) -> None:
    _last_run[probe_name] = time.time()


async def _read_setting(pool: Any, key: str, default: str) -> str:
    """Read an app_settings value. Never raises."""
    try:
        value = await pool.fetchval(
            "SELECT value FROM app_settings WHERE key = $1 AND is_active = TRUE",
            key,
        )
    except Exception:
        return default
    if value is None:
        return default
    return str(value).strip() or default


async def probe_post_performance(pool: Any, notify_fn: Any) -> dict:
    """Classify post_performance snapshots and surface broken posts.

    Best-effort: never raises. Returns ``{"ok": False, "detail": ...}``
    on DB error so the brain cycle can keep going.
    """
    import inspect

    async def _maybe_await(value: Any) -> Any:
        if inspect.isawaitable(value):
            return await value
        return value

    enabled = (
        await _read_setting(pool, "post_performance_probe_enabled", "true")
    ).lower()
    if enabled in ("false", "0", "no", "off"):
        return {"ok": True, "detail": "disabled via app_settings"}

    interval = int(
        await _read_setting(
            pool, "post_performance_probe_interval_minutes", "1440"
        ) or 1440
    )
    if not _is_due("post_performance", interval):
        return {"ok": True, "detail": "not due yet"}
    _mark_run("post_performance")

    broken_min_age_days = int(
        await _read_setting(
            pool, "post_performance_broken_min_age_days", "30"
        ) or 30
    )
    fading_ratio = float(
        await _read_setting(
            pool, "post_performance_fading_threshold_ratio", "0.5"
        ) or 0.5
    )
    spike_multiplier = float(
        await _read_setting(
            pool, "post_performance_performing_spike_multiplier", "3.0"
        ) or 3.0
    )

    try:
        # Latest snapshot per slug, only for posts old enough to matter.
        rows = await pool.fetch(
            """
            SELECT DISTINCT ON (pp.slug)
              pp.slug,
              pp.views_1d,
              pp.views_7d,
              pp.views_30d,
              pp.views_total,
              pp.avg_time_on_page_seconds,
              pp.measured_at,
              p.published_at
            FROM post_performance pp
            JOIN posts p ON p.slug = pp.slug AND p.status = 'published'
            WHERE p.published_at <= NOW() - ($1::int || ' days')::interval
            ORDER BY pp.slug, pp.measured_at DESC
            """,
            broken_min_age_days,
        )
    except Exception as e:
        logger.warning("[POST_PERF_PROBE] DB read failed: %s", e)
        return {"ok": False, "detail": f"db read failed: {e}"}

    if not rows:
        return {"ok": True, "detail": "no post_performance snapshots to evaluate"}

    broken: list[str] = []
    fading: list[str] = []
    performing: list[str] = []

    for row in rows:
        slug = row["slug"]
        v1 = row["views_1d"] or 0
        v7 = row["views_7d"] or 0
        v30 = row["views_30d"] or 0

        if v30 == 0:
            broken.append(slug)
        elif v7 < (v30 / 4) * fading_ratio:
            fading.append(slug)

        if v7 > 0:
            daily_avg = v7 / 7
            if daily_avg > 0 and v1 > spike_multiplier * daily_avg:
                performing.append(slug)

    logger.info(
        "[POST_PERF_PROBE] %d posts evaluated — broken=%d fading=%d performing=%d",
        len(rows), len(broken), len(fading), len(performing),
    )
    if fading:
        logger.debug(
            "[POST_PERF_PROBE] Fading posts (%d): %s",
            len(fading), ", ".join(fading[:10]),
        )
    if performing:
        logger.debug(
            "[POST_PERF_PROBE] Performing posts (%d): %s",
            len(performing), ", ".join(performing[:10]),
        )

    if not broken:
        return {
            "ok": True,
            "detail": (
                f"ok: {len(rows)} posts evaluated, "
                f"fading={len(fading)}, performing={len(performing)}"
            ),
            "broken_count": 0,
            "fading_count": len(fading),
            "performing_count": len(performing),
        }

    # Page the operator for broken posts — these need investigation.
    broken_list = "\n".join(f"  - {s}" for s in broken[:20])
    more = f"\n  - …and {len(broken) - 20} more" if len(broken) > 20 else ""
    body = (
        f"BROKEN POSTS — {len(broken)} published post(s) with 0 views "
        f"in the last 30 days (published >{broken_min_age_days}d ago):\n\n"
        f"{broken_list}{more}\n\n"
        "Possible causes: indexing not yet complete, slug mismatch, "
        "redirect broken, or the Cloudflare Analytics tap has stalled.\n\n"
        "Check: poindexter analytics recent / Cloudflare dash / GSC coverage report."
    )
    try:
        await _maybe_await(notify_fn(body))
    except Exception as e:
        logger.warning("[POST_PERF_PROBE] notify_fn failed: %s", e)

    logger.warning(
        "[POST_PERF_PROBE] PAGED — %d broken posts", len(broken)
    )
    return {
        "ok": True,
        "detail": f"paged: {len(broken)} broken posts",
        "broken_count": len(broken),
        "fading_count": len(fading),
        "performing_count": len(performing),
        "broken_slugs": broken[:20],
    }
