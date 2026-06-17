"""#1460 PR1: the approved-undispatched dispatch query must return ONE row per
(post, medium) even when a post has multiple matching video assets — else the
post double-uploads to YouTube post-cutover. Real-DB test: the de-dup is SQL.

NOTE (PR1 vs PR2 join semantics): under the PR1 query the join is still the
CASE map (medium 'video' -> type 'video_long'), so the multi-match scenario is
two 'video_long' rows. PR2 flips the join to identity (mas.type = ma.medium);
when that lands, this fixture's asset rows move to type='video'.
"""
from __future__ import annotations

import pytest

pytestmark = [
    pytest.mark.integration_db,
    pytest.mark.asyncio(loop_scope="session"),
]


async def test_approved_undispatched_dedups_to_one_per_post(test_txn) -> None:
    from services.jobs.media_distribute import _APPROVED_UNDISPATCHED_SQL

    post_id = await test_txn.fetchval(
        """
        INSERT INTO posts (id, title, slug, content, excerpt, seo_keywords, status, published_at)
        VALUES (gen_random_uuid(), 'Dedup Post', 'dedup-post-1460',
                'body', 'excerpt', 'k1,k2', 'published', NOW())
        RETURNING id
        """,
    )
    # One approved, undispatched, non-grandfather approval for the long medium.
    await test_txn.execute(
        """
        INSERT INTO media_approvals (post_id, medium, status, decided_by, dispatched_at)
        VALUES ($1, 'video', 'approved', 'operator', NULL)
        """,
        post_id,
    )
    # Two competing long-form pipeline assets (PR1 CASE-join matches video_long):
    # the one carrying a YouTube id must win; both have a non-empty storage_path
    # so both are eligible.
    await test_txn.execute(
        """
        INSERT INTO media_assets (post_id, type, source, storage_provider, storage_path, url, platform_video_ids)
        VALUES
          ($1, 'video_long', 'pipeline', 'local', '/tmp/pipe_old.mp4', '', '{}'::jsonb),
          ($1, 'video_long', 'pipeline', 'local', '/tmp/pipe_yt.mp4',  '', '{"youtube":"abc123"}'::jsonb)
        """,
        post_id,
    )

    rows = await test_txn.fetch(_APPROVED_UNDISPATCHED_SQL, ["video", "video_short"], 50)
    mine = [r for r in rows if r["post_id"] == str(post_id)]
    assert len(mine) == 1, f"expected 1 row for the post, got {len(mine)}"
    assert mine[0]["storage_path"] == "/tmp/pipe_yt.mp4"  # YouTube-id row wins
