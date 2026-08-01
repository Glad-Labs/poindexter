"""FlagBotPageViewsJob flags flood pairs, spares sparse traffic, recomputes view_count.

Runs against the integration_db harness (full migration tree applied, so the
20260710 is_bot migration is in place). Skips when no Postgres is reachable.
"""
from __future__ import annotations

import pytest

from services.jobs.flag_bot_page_views import FlagBotPageViewsJob

pytestmark = [
    pytest.mark.integration_db,
    pytest.mark.asyncio(loop_scope="session"),
]


class _FakeSiteConfig:
    def __init__(self, values):
        self._v = values

    def get(self, key, default=None):
        return self._v.get(key, default)


def _config():
    return {
        "_site_config": _FakeSiteConfig(
            {
                "beacon_bot_flag_enabled": "true",
                "beacon_flood_window_hours": "24",
                "beacon_flood_cap_per_window": "20",
                "beacon_flood_backfill_cap": "30",
                "beacon_sweep_max_distinct_paths": "25",
            }
        )
    }


async def _reset(conn):
    await conn.execute("DELETE FROM page_views")
    await conn.execute(
        "UPDATE app_settings SET value='' WHERE key='beacon_bot_flag_backfilled'"
    )


async def test_flood_pair_flagged_sparse_spared(test_pool) -> None:
    async with test_pool.acquire() as conn:
        async with conn.transaction():
            await _reset(conn)
            # 25 hits from one UA on one path within the window → over cap 20.
            await conn.execute(
                "INSERT INTO page_views (path, slug, user_agent, created_at) "
                "SELECT '/posts/hot', 'hot', 'flood-ua', now() FROM generate_series(1, 25)"
            )
            # 3 sparse human hits, distinct UAs, under cap.
            await conn.execute(
                "INSERT INTO page_views (path, slug, user_agent, created_at) VALUES "
                "('/posts/calm', 'calm', 'ua-1', now()), "
                "('/posts/calm', 'calm', 'ua-2', now()), "
                "('/posts/calm', 'calm', 'ua-3', now())"
            )

    result = await FlagBotPageViewsJob().run(test_pool, _config())
    assert result.ok is True

    async with test_pool.acquire() as conn:
        flood_flagged = await conn.fetchval(
            "SELECT COUNT(*) FROM page_views WHERE slug='hot' AND is_bot=true"
        )
        calm_flagged = await conn.fetchval(
            "SELECT COUNT(*) FROM page_views WHERE slug='calm' AND is_bot=true"
        )
        human = await conn.fetchval("SELECT COUNT(*) FROM page_views_human")
    assert flood_flagged == 25   # whole group flagged
    assert calm_flagged == 0     # sparse spared
    assert human == 3            # only the calm hits remain human


async def test_cap_boundary(test_pool) -> None:
    async with test_pool.acquire() as conn:
        async with conn.transaction():
            await _reset(conn)
            # Exactly at cap (20) → NOT flagged (HAVING COUNT > cap).
            await conn.execute(
                "INSERT INTO page_views (path, slug, user_agent, created_at) "
                "SELECT '/posts/edge', 'edge', 'edge-ua', now() FROM generate_series(1, 20)"
            )
    await FlagBotPageViewsJob().run(test_pool, _config())
    async with test_pool.acquire() as conn:
        flagged = await conn.fetchval(
            "SELECT COUNT(*) FROM page_views WHERE slug='edge' AND is_bot=true"
        )
    assert flagged == 0


async def test_view_count_recomputed_from_human(test_pool) -> None:
    async with test_pool.acquire() as conn:
        async with conn.transaction():
            await _reset(conn)
            await conn.execute("DELETE FROM posts WHERE slug IN ('hot', 'clean')")
            await conn.execute(
                "INSERT INTO posts (slug, title, status, view_count) VALUES "
                "('hot', 'Hot', 'published', 999), "
                "('clean', 'Clean', 'published', 0)"
            )
            await conn.execute(
                "INSERT INTO page_views (path, slug, user_agent, created_at) "
                "SELECT '/posts/hot', 'hot', 'flood-ua', now() FROM generate_series(1, 25)"
            )
            await conn.execute(
                "INSERT INTO page_views (path, slug, user_agent, created_at) VALUES "
                "('/posts/clean', 'clean', 'ua-a', now()), "
                "('/posts/clean', 'clean', 'ua-b', now())"
            )
    await FlagBotPageViewsJob().run(test_pool, _config())
    async with test_pool.acquire() as conn:
        hot = await conn.fetchval("SELECT view_count FROM posts WHERE slug='hot'")
        clean = await conn.fetchval("SELECT view_count FROM posts WHERE slug='clean'")
    assert hot == 0    # bot-only post reset to 0
    assert clean == 2  # human views only


async def test_backfill_sentinel_runs_once(test_pool) -> None:
    async with test_pool.acquire() as conn:
        async with conn.transaction():
            await _reset(conn)
    result1 = await FlagBotPageViewsJob().run(test_pool, _config())
    async with test_pool.acquire() as conn:
        sentinel = await conn.fetchval(
            "SELECT value FROM app_settings WHERE key='beacon_bot_flag_backfilled'"
        )
    assert result1.metrics["backfill_ran"] == 1
    assert (sentinel or "").lower() == "true"
    # Second run: sentinel set → no backfill.
    result2 = await FlagBotPageViewsJob().run(test_pool, _config())
    assert result2.metrics["backfill_ran"] == 0


async def test_path_sweep_flags_window_only(test_pool) -> None:
    """The distinct-paths sweep (poindexter#973): a UA visiting >cap distinct
    paths in the window has its WINDOW rows flagged — rows outside the window
    and modest browsing stay human (bare UA strings are shared across people)."""
    async with test_pool.acquire() as conn:
        async with conn.transaction():
            await _reset(conn)
            # Full-site crawl: 30 distinct paths in-window (over cap 25).
            await conn.execute(
                "INSERT INTO page_views (path, slug, user_agent, created_at) "
                "SELECT '/posts/p' || g, 'p' || g, 'crawler-ua', now() "
                "FROM generate_series(1, 30) g"
            )
            # Same UA, 2 rows OUTSIDE the 24h window — must stay human.
            await conn.execute(
                "INSERT INTO page_views (path, slug, user_agent, created_at) "
                "VALUES ('/posts/old1', 'old1', 'crawler-ua', now() - interval '3 days'), "
                "('/posts/old2', 'old2', 'crawler-ua', now() - interval '3 days')"
            )
            # Ordinary reader: 5 distinct paths, far under cap.
            await conn.execute(
                "INSERT INTO page_views (path, slug, user_agent, created_at) "
                "SELECT '/posts/r' || g, 'r' || g, 'reader-ua', now() "
                "FROM generate_series(1, 5) g"
            )
            # Boundary: exactly 25 distinct paths → NOT flagged (HAVING > cap).
            await conn.execute(
                "INSERT INTO page_views (path, slug, user_agent, created_at) "
                "SELECT '/posts/b' || g, 'b' || g, 'boundary-ua', now() "
                "FROM generate_series(1, 25) g"
            )

    result = await FlagBotPageViewsJob().run(test_pool, _config())
    assert result.ok is True

    async with test_pool.acquire() as conn:
        swept = await conn.fetchval(
            "SELECT COUNT(*) FROM page_views "
            "WHERE user_agent='crawler-ua' AND is_bot=true "
            "AND bot_reason='sweep:ua_distinct_paths'"
        )
        old_spared = await conn.fetchval(
            "SELECT COUNT(*) FROM page_views "
            "WHERE user_agent='crawler-ua' AND is_bot=false"
        )
        reader = await conn.fetchval(
            "SELECT COUNT(*) FROM page_views WHERE user_agent='reader-ua' AND is_bot=true"
        )
        boundary = await conn.fetchval(
            "SELECT COUNT(*) FROM page_views WHERE user_agent='boundary-ua' AND is_bot=true"
        )
    assert swept == 30       # the crawl's window rows, all flagged
    assert old_spared == 2   # outside-window rows for the same UA stay human
    assert reader == 0       # modest browsing spared
    assert boundary == 0     # exactly-at-cap spared (HAVING strictly greater)
