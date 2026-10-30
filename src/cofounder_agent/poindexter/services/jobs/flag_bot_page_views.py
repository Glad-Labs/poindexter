"""FlagBotPageViewsJob — flag stealth scraper hits in page_views.

The sync job (sync_cloudflare_analytics) drops declared-crawler UAs at ingest,
but stealth scrapers present a normal browser UA and slip through, inflating the
first-party beacon KPI ~10x. Declared bots can be caught per-row; a stealth
flood can only be caught over a WINDOW — a single 5-min sync batch can't tell one
(user_agent, path) has been hit hundreds of times over weeks. So this runs as a
periodic sweep over accumulated rows.

Per run (gated by app_settings.beacon_bot_flag_enabled):

1. Windowed flood pass — any (user_agent, path) pair with COUNT(*) over
   beacon_flood_cap_per_window inside beacon_flood_window_hours has its ENTIRE
   history flagged is_bot=true (whole-group semantics; biases to under-count over
   inflation). Flagging is monotonic: only ever sets is_bot true, never resets.
2. Windowed path-sweep pass (poindexter#973) — any user_agent hitting more than
   beacon_sweep_max_distinct_paths DISTINCT paths inside the same window has its
   WINDOW rows flagged. Catches full-site crawlers that visit each page once —
   invisible to the pair cap by construction (147 hits / 145 paths, 2026-07-26,
   poisoned the traffic-anomaly baseline for a week). Window-scoped on purpose:
   bare UA strings are shared across real humans, so whole-history flagging
   would over-flag — and for the same reason this pass has NO backfill twin.
3. Referrer-host pass — any row whose referrer host (``www.`` stripped) is
   listed in beacon_bot_referrer_hosts is flagged, across ALL history. A
   referrer is a claim the client makes, so spam that forges one site's
   homepage onto every hit is a stronger signature than any UA count: the
   2026-10-26 ``www.aocr.org`` run (82 hits, one Chrome UA, 27 old posts over
   four days) never hit 26 distinct paths in one window, so the sweep could not
   see it, and it was half the week's "human" views. Empty by default; an
   operator names a host only after looking at its rows.
4. One-time backfill — sentinel-guarded (beacon_bot_flag_backfilled), same
   pair grouping over all history at beacon_flood_backfill_cap, to catch bots
   that flooded historically but aren't active in the current window.
5. Recompute posts.view_count authoritatively from page_views_human (this job is
   the single writer of view_count; the sync job's incremental bump is removed in
   the same PR). Bot-only posts reset to 0.

Reader surfaces (console /api/analytics/views, lab_outcomes_v1, posts.view_count)
read the human view; liveness/anomaly/freshness signals stay on raw page_views.
"""
from __future__ import annotations

import logging
from typing import Any

from poindexter.plugins.job import JobResult
from poindexter.utils.exception_format import describe_exception

logger = logging.getLogger(__name__)

_WINDOW_UPDATE = """
WITH flooded AS (
    SELECT user_agent, path
    FROM page_views
    WHERE created_at >= now() - ($1 || ' hours')::interval
      AND is_bot = false
    GROUP BY user_agent, path
    HAVING COUNT(*) > $2
)
UPDATE page_views pv
SET is_bot = true, bot_reason = 'flood:ua_path', flagged_at = now()
FROM flooded f
WHERE pv.user_agent IS NOT DISTINCT FROM f.user_agent
  AND pv.path IS NOT DISTINCT FROM f.path
  AND pv.is_bot = false
"""

_SWEEP_UPDATE = """
WITH sweepers AS (
    SELECT user_agent
    FROM page_views
    WHERE created_at >= now() - ($1 || ' hours')::interval
      AND is_bot = false
    GROUP BY user_agent
    HAVING COUNT(DISTINCT path) > $2
)
UPDATE page_views pv
SET is_bot = true, bot_reason = 'sweep:ua_distinct_paths', flagged_at = now()
FROM sweepers s
WHERE pv.user_agent IS NOT DISTINCT FROM s.user_agent
  AND pv.created_at >= now() - ($1 || ' hours')::interval
  AND pv.is_bot = false
"""

_REFERRER_UPDATE = """
UPDATE page_views
SET is_bot = true, bot_reason = 'referrer:blocklist', flagged_at = now()
WHERE is_bot = false
  AND referrer IS NOT NULL
  AND regexp_replace(
        lower(split_part(split_part(referrer, '://', 2), '/', 1)),
        '^www\\.', ''
      ) = ANY($1::text[])
"""

_BACKFILL_UPDATE = """
WITH flooded AS (
    SELECT user_agent, path
    FROM page_views
    WHERE is_bot = false
    GROUP BY user_agent, path
    HAVING COUNT(*) > $1
)
UPDATE page_views pv
SET is_bot = true, bot_reason = 'flood:ua_path:backfill', flagged_at = now()
FROM flooded f
WHERE pv.user_agent IS NOT DISTINCT FROM f.user_agent
  AND pv.path IS NOT DISTINCT FROM f.path
  AND pv.is_bot = false
"""

_RECOMPUTE_VIEW_COUNT = """
UPDATE posts p
SET view_count = COALESCE(agg.c, 0)
FROM posts base
LEFT JOIN (
    SELECT slug, COUNT(*)::int AS c
    FROM page_views_human
    WHERE slug IS NOT NULL AND slug <> ''
    GROUP BY slug
) agg ON agg.slug = base.slug
WHERE p.id = base.id
  AND p.view_count IS DISTINCT FROM COALESCE(agg.c, 0)
"""

_SENTINEL_UPSERT = """
INSERT INTO app_settings (key, value, category, description, is_active, is_secret)
VALUES (
    'beacon_bot_flag_backfilled', 'true', 'cloudflare',
    'Set true after FlagBotPageViewsJob runs its one-time all-history bot backfill.',
    true, false
)
ON CONFLICT (key) DO UPDATE SET value = EXCLUDED.value
"""


def parse_referrer_hosts(raw: str | None) -> list[str]:
    """Normalize the beacon_bot_referrer_hosts CSV to bare lowercase hosts.

    Accepts ``aocr.org``, ``www.aocr.org`` or a pasted ``https://www.aocr.org/``
    and returns ``aocr.org`` for each, matching how the SQL normalizes the
    stored referrer.
    """
    hosts: list[str] = []
    for item in (raw or "").split(","):
        h = item.strip().lower()
        if "://" in h:
            h = h.split("://", 1)[1]
        h = h.split("/", 1)[0]
        if h.startswith("www."):
            h = h[4:]
        if h and h not in hosts:
            hosts.append(h)
    return hosts


def _rowcount(status: Any) -> int:
    """Parse asyncpg's 'UPDATE N' command tag into an int."""
    try:
        return int(str(status).split()[-1])
    except (ValueError, IndexError):
        return 0


class FlagBotPageViewsJob:
    name = "flag_bot_page_views"
    description = (
        "Flag stealth-scraper page_views (windowed (user_agent, path) flood-cap) "
        "and recompute posts.view_count from the human view."
    )
    schedule = "every 15 minutes"
    idempotent = True

    async def run(self, pool: Any, config: dict[str, Any]) -> JobResult:
        sc = config.get("_site_config")
        if sc is None:
            return JobResult(ok=True, detail="no _site_config — skipping", changes_made=0)

        enabled = (sc.get("beacon_bot_flag_enabled", "true") or "true").strip().lower()
        if enabled != "true":
            return JobResult(
                ok=True,
                detail="beacon_bot_flag_enabled is disabled — skipping",
                changes_made=0,
            )

        window_hours = int((sc.get("beacon_flood_window_hours", "24") or "24").strip())
        window_cap = int((sc.get("beacon_flood_cap_per_window", "20") or "20").strip())
        backfill_cap = int((sc.get("beacon_flood_backfill_cap", "30") or "30").strip())
        sweep_cap = int(
            (sc.get("beacon_sweep_max_distinct_paths", "25") or "25").strip()
        )

        referrer_hosts = parse_referrer_hosts(sc.get("beacon_bot_referrer_hosts", ""))

        flagged = 0
        sweep_flagged = 0
        referrer_flagged = 0
        did_backfill = False
        try:
            async with pool.acquire() as conn:
                async with conn.transaction():
                    win = await conn.execute(_WINDOW_UPDATE, str(window_hours), window_cap)
                    flagged += _rowcount(win)

                    swept = await conn.execute(_SWEEP_UPDATE, str(window_hours), sweep_cap)
                    sweep_flagged = _rowcount(swept)
                    flagged += sweep_flagged

                    if referrer_hosts:
                        ref = await conn.execute(_REFERRER_UPDATE, referrer_hosts)
                        referrer_flagged = _rowcount(ref)
                        flagged += referrer_flagged

                    sentinel = await conn.fetchval(
                        "SELECT value FROM app_settings WHERE key = 'beacon_bot_flag_backfilled'"
                    )
                    if (sentinel or "").strip().lower() != "true":
                        back = await conn.execute(_BACKFILL_UPDATE, backfill_cap)
                        flagged += _rowcount(back)
                        await conn.execute(_SENTINEL_UPSERT)
                        did_backfill = True

                    await conn.execute(_RECOMPUTE_VIEW_COUNT)
        except Exception as e:  # noqa: BLE001 — surface as a failed JobResult, not a crash
            logger.exception("[FLAG_BOT_PV] sweep failed: %s", describe_exception(e))
            return JobResult(ok=False, detail=describe_exception(e), changes_made=0)

        return JobResult(
            ok=True,
            detail=(
                f"flagged {flagged} bot page_views "
                f"(sweep={sweep_flagged}, referrer={referrer_flagged}, "
                f"backfill={did_backfill})"
            ),
            changes_made=flagged,
            metrics={
                "rows_flagged": flagged,
                "sweep_flagged": sweep_flagged,
                "referrer_flagged": referrer_flagged,
                "backfill_ran": int(did_backfill),
            },
        )
