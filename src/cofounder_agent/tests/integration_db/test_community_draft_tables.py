"""Schema guard for the community-draft tables (subreddit_profiles,
community_post_drafts). Runs against the disposable ``test_pool``; skips when
no live Postgres is available."""
from __future__ import annotations

import pytest

pytestmark = [
    pytest.mark.integration_db,
    pytest.mark.asyncio(loop_scope="session"),
]


async def _columns(pool, table: str) -> set[str]:
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            "SELECT column_name FROM information_schema.columns WHERE table_name = $1",
            table,
        )
    return {r["column_name"] for r in rows}


async def test_subreddit_profiles_columns(test_pool):
    cols = await _columns(test_pool, "subreddit_profiles")
    assert {
        "id", "subreddit", "enabled", "content_types", "post_type", "self_promo",
        "flair", "min_karma", "min_account_age_days", "rules_summary", "tone_notes",
        "cadence_cap_days", "created_at", "updated_at",
    } <= cols


async def test_community_post_drafts_columns(test_pool):
    cols = await _columns(test_pool, "community_post_drafts")
    assert {
        "id", "target", "title", "body", "post_type", "source_post_id",
        "warnings", "status", "posted_url", "model", "created_at", "updated_at",
    } <= cols


async def test_subreddit_profiles_ship_empty(test_pool):
    """Operator subreddit seeds live in the overlay, not OSS baseline."""
    async with test_pool.acquire() as conn:
        n = await conn.fetchval("SELECT COUNT(*) FROM subreddit_profiles")
    assert n == 0


async def test_subreddit_unique(test_pool):
    async with test_pool.acquire() as conn:
        with pytest.raises(Exception):
            async with conn.transaction():
                await conn.execute("INSERT INTO subreddit_profiles (subreddit) VALUES ('dup-test')")
                await conn.execute("INSERT INTO subreddit_profiles (subreddit) VALUES ('dup-test')")
