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
  - broken posts  → a Discord notice (info_fn) — an SEO/content signal,
    worth investigating same-day, not a page. (2026-09-25: this doc
    previously said "notify_fn (Telegram) — high-priority"; in prod it
    paged 120 times in the 30 days to 2026-09-25, 111 of them within 15
    minutes of a brain restart. Its last-run time was in-process until
    #4116 persisted it (probe_schedule.py), so every restart re-ran the
    "daily" probe and re-sent the whole list,
    one post until 2026-09-04 and then 78 growing to 129: repeats, not
    120 new findings. See ``poindexter.brain.probe_severity``, whose
    default for any probe not named "critical" — including this one — is
    the non-paging "warning".)
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
from typing import Any

from poindexter.brain import probe_schedule, probe_severity

logger = logging.getLogger("brain.post_performance_probe")


# Last-run times survive a brain restart (see probe_schedule.py).
def _is_due(probe_name: str, interval_minutes: int) -> bool:
    return probe_schedule.schedule.is_due(probe_name, interval_minutes * 60)


async def _mark_run(pool: Any, probe_name: str) -> None:
    await probe_schedule.schedule.mark_run(pool, probe_name)


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


async def probe_post_performance(pool: Any, notify_fn: Any, *, info_fn: Any = None) -> dict:
    """Classify post_performance snapshots and surface broken posts.

    The broken-posts finding goes through ``info_fn`` (Discord), falling
    back to ``notify_fn`` when ``info_fn`` is omitted — see the module
    docstring's "Alert routing" section for why.

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
    await probe_schedule.schedule.load(pool)
    if not _is_due("post_performance", interval):
        return {"ok": True, "detail": "not due yet"}
    await _mark_run(pool, "post_performance")

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

    # Notify the operator of broken posts — these need investigation, but
    # not urgently (see the module docstring's "Alert routing" section).
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
    sent_via = "notify_fn"
    try:
        sender = await probe_severity.sender_for(pool, "post_performance", notify_fn, info_fn)
        sent_via = "info_fn/notice" if sender is not notify_fn else "notify_fn/page"
        await _maybe_await(sender(body))
    except Exception as e:
        logger.warning("[POST_PERF_PROBE] notify failed: %s", e)

    logger.warning(
        "[POST_PERF_PROBE] sent (%s) — %d broken posts", sent_via, len(broken)
    )
    return {
        "ok": True,
        "detail": f"sent ({sent_via}): {len(broken)} broken posts",
        "broken_count": len(broken),
        "fading_count": len(fading),
        "performing_count": len(performing),
        "broken_slugs": broken[:20],
    }
