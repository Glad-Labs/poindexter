"""End-to-end round-trips against real Postgres: profile CRUD, a draft
lifecycle, content-type suggest, and CSV export→import (verifies text[] columns
survive the trip). Skips cleanly when no live Postgres is available."""
from __future__ import annotations

import pytest

from services.community_drafts import (
    SubredditProfile,
    add_profile,
    create_draft,
    get_draft,
    get_profile,
    mark_posted,
    suggest_subreddits_for_post,
)
from services.subreddit_import import export_csv, import_csv

pytestmark = [
    pytest.mark.integration_db,
    pytest.mark.asyncio(loop_scope="session"),
]


async def test_profile_insert_and_read_back(test_pool):
    async with test_pool.acquire() as conn:
        with pytest.raises(RuntimeError, match="rollback-for-test-isolation"):
            async with conn.transaction():
                await add_profile(conn, SubredditProfile(
                    subreddit="itest-localllama", content_types=["ai-ml", "pc-hardware"],
                    post_type="text", self_promo="strict", flair="Discussion", min_karma=100,
                ))
                p = await get_profile(conn, "itest-localllama")
                assert p is not None
                assert p.content_types == ["ai-ml", "pc-hardware"]   # text[] round-trip
                assert p.min_karma == 100 and p.flair == "Discussion"
                raise RuntimeError("rollback-for-test-isolation")


async def test_draft_lifecycle(test_pool):
    async with test_pool.acquire() as conn:
        with pytest.raises(RuntimeError, match="rollback-for-test-isolation"):
            async with conn.transaction():
                did = await create_draft(
                    conn, target="reddit:itest", body="native body",
                    title="T", post_type="text",
                    warnings=["self-promo=strict: post as native text, no blog link"],
                    model="gemma",
                )
                d = await get_draft(conn, did)
                assert d.status == "draft" and d.warnings[0].startswith("self-promo=strict")
                assert await mark_posted(conn, did, url="https://reddit.com/r/itest/x") is True
                d2 = await get_draft(conn, did)
                assert d2.status == "posted" and d2.posted_url.endswith("/x")
                raise RuntimeError("rollback-for-test-isolation")


async def test_suggest_matches_on_content_type(test_pool):
    async with test_pool.acquire() as conn:
        with pytest.raises(RuntimeError, match="rollback-for-test-isolation"):
            async with conn.transaction():
                post_id = await conn.fetchval(
                    "INSERT INTO posts (title, slug, content, status) "
                    "VALUES ('t','itest-suggest-slug','b','published') RETURNING id"
                )
                await conn.execute(
                    "INSERT INTO post_content_types (post_id, content_type) VALUES ($1,'ai-ml')",
                    post_id,
                )
                await add_profile(conn, SubredditProfile(
                    subreddit="itest-match", content_types=["ai-ml"], enabled=True))
                await add_profile(conn, SubredditProfile(
                    subreddit="itest-nomatch", content_types=["gaming"], enabled=True))
                out = await suggest_subreddits_for_post(conn, str(post_id))
                assert "itest-match" in out and "itest-nomatch" not in out
                raise RuntimeError("rollback-for-test-isolation")


async def test_csv_export_import_roundtrip(test_pool, tmp_path):
    async with test_pool.acquire() as conn:
        with pytest.raises(RuntimeError, match="rollback-for-test-isolation"):
            async with conn.transaction():
                await add_profile(conn, SubredditProfile(
                    subreddit="itest-csv", content_types=["ai-ml", "gaming"],
                    post_type="either", self_promo="moderate", min_karma=50,
                    rules_summary="No memes.", cadence_cap_days=7))
                text = await export_csv(conn)
                assert "itest-csv" in text and "ai-ml;gaming" in text
                f = tmp_path / "out.csv"
                f.write_text(text, encoding="utf-8")
                # re-import with --force updates in place (idempotent round-trip)
                rep = await import_csv(conn, str(f), force=True)
                assert any(r.subreddit == "itest-csv" and r.status == "updated"
                           for r in rep.rows)
                back = await get_profile(conn, "itest-csv")
                assert back.content_types == ["ai-ml", "gaming"]     # array survived CSV
                assert back.min_karma == 50 and back.cadence_cap_days == 7
                raise RuntimeError("rollback-for-test-isolation")
