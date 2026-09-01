"""integration_db: the own-site distribution target is a sentinel, not a domain.

``pipeline_distributions.target`` names WHERE an artifact of a task landed —
a platform, except for the one row saying the post itself went live on the site
this install publishes. That row used to carry the literal ``'gladlabs.io'``:
the source operator's own domain, hardcoded at three write sites and matched by
name in both view bodies, the yield query and two Grafana panels
(poindexter#1038).

It never broke a fresh install — the literal matched on both the write and the
read side — which is exactly why it lasted. The damage is that every fork
labels its own publishes with someone else's brand, and every "not the site
itself" filter reads as ``target <> '<one operator's domain>'``.

The read side is a VIEW, so it cannot consult ``app_settings.site_domain`` to
learn an install's real domain. A sentinel is the only encoding that works at
both ends.

The harness applies the full migration chain against a real Postgres, so these
assert the END STATE rather than the migration's steps: both views resolve the
own-site columns for the sentinel, and — because a row written by pre-cutover
code can still arrive (an older dump restored, a publish inside a rollback
window) — for the legacy spelling too.
"""

from __future__ import annotations

import uuid

import pytest

from services.pipeline_db import LEGACY_SITE_TARGETS, SITE_TARGET, PipelineDB

pytestmark = [
    pytest.mark.integration_db,
    pytest.mark.asyncio(loop_scope="session"),
]

_OWN_SITE_COLUMNS = ("post_id", "post_slug", "published_at")
_VIEWS = ("content_tasks", "pipeline_tasks_view")


async def _seed_task(conn, task_id: str) -> None:
    """pipeline_distributions.task_id is a FK to pipeline_tasks."""
    await conn.execute(
        "INSERT INTO pipeline_tasks (task_id, topic, status) "
        "VALUES ($1, 'own-site target test', 'completed') "
        "ON CONFLICT (task_id) DO NOTHING",
        task_id,
    )


async def _distribute(conn, task_id: str, target: str, slug: str) -> None:
    await conn.execute(
        "INSERT INTO pipeline_distributions "
        "  (task_id, target, medium, status, post_slug, published_at) "
        "VALUES ($1, $2, 'default', 'published', $3, NOW())",
        task_id, target, slug,
    )


@pytest.mark.parametrize("view", _VIEWS)
async def test_view_resolves_own_site_columns_for_the_sentinel(test_txn, view) -> None:
    """The whole point of the row: without it a published task reads as
    unpublished, because these three columns come from nowhere else."""
    task_id = f"own-site-{uuid.uuid4().hex[:12]}"
    await _seed_task(test_txn, task_id)
    await _distribute(test_txn, task_id, SITE_TARGET, "sentinel-post")

    row = await test_txn.fetchrow(
        f"SELECT post_slug, published_at FROM {view} WHERE task_id = $1",  # nosec B608 - view name is parametrized from a module constant, never input
        task_id,
    )
    assert row is not None, f"{view} lost the task row entirely"
    assert row["post_slug"] == "sentinel-post"
    assert row["published_at"] is not None


@pytest.mark.parametrize("view", _VIEWS)
@pytest.mark.parametrize("legacy", LEGACY_SITE_TARGETS)
async def test_view_still_resolves_a_pre_cutover_row(test_txn, view, legacy) -> None:
    """Back-compat, and the reason the readers accept a list rather than one
    value. Writers only produce the sentinel now, so a legacy row means a DB
    restored from an older dump or a publish inside a rollback window — it must
    still resolve, not silently read as an unpublished task.
    """
    task_id = f"own-site-legacy-{uuid.uuid4().hex[:12]}"
    await _seed_task(test_txn, task_id)
    await _distribute(test_txn, task_id, legacy, "legacy-post")

    row = await test_txn.fetchrow(
        f"SELECT post_slug FROM {view} WHERE task_id = $1",  # nosec B608 - view name is parametrized from a module constant, never input
        task_id,
    )
    assert row is not None and row["post_slug"] == "legacy-post", (
        f"{view} dropped a pre-cutover own-site row — every task published "
        f"before the sentinel would read as unpublished"
    )


@pytest.mark.parametrize("view", _VIEWS)
async def test_a_platform_row_is_not_mistaken_for_the_site(test_txn, view) -> None:
    """The predicate widened; it must not have widened to everything. A YouTube
    upload is not the post going live, and must not fill these columns."""
    task_id = f"own-site-platform-{uuid.uuid4().hex[:12]}"
    await _seed_task(test_txn, task_id)
    await _distribute(test_txn, task_id, "youtube", "youtube-post")

    row = await test_txn.fetchrow(
        f"SELECT {', '.join(_OWN_SITE_COLUMNS)} FROM {view} WHERE task_id = $1",  # nosec B608 - column and view names are module constants, never input
        task_id,
    )
    assert row is not None
    assert all(row[c] is None for c in _OWN_SITE_COLUMNS), (
        "a platform distribution filled the own-site columns — the view's "
        "target predicate is too wide"
    )


async def test_the_writer_and_the_views_agree(test_txn) -> None:
    """End to end through the real write helper. The write site and the read
    site were only ever consistent by coincidence of sharing a literal; this is
    the assertion that keeps them consistent by construction."""
    task_id = f"own-site-e2e-{uuid.uuid4().hex[:12]}"
    await _seed_task(test_txn, task_id)

    class _Conn:
        """PipelineDB wants a pool; the fixture hands out a transaction."""

        @staticmethod
        async def execute(sql, *args):
            return await test_txn.execute(sql, *args)

    await PipelineDB(_Conn()).add_distribution(
        task_id, SITE_TARGET, post_slug="e2e-post", status="published",
    )

    written = await test_txn.fetchval(
        "SELECT target FROM pipeline_distributions WHERE task_id = $1", task_id,
    )
    assert written == SITE_TARGET

    for view in _VIEWS:
        slug = await test_txn.fetchval(
            f"SELECT post_slug FROM {view} WHERE task_id = $1",  # nosec B608 - view name is a module constant, never input
            task_id,
        )
        assert slug == "e2e-post", f"{view} did not see what the writer wrote"


async def test_no_row_is_left_carrying_the_legacy_target(test_txn) -> None:
    """After the migration chain, the seeded corpus holds no legacy own-site
    row. A survivor is not a failure — the views read both — but it means the
    retarget hit a conflict, which the write path can no longer produce.
    """
    stranded = await test_txn.fetch(
        "SELECT DISTINCT target FROM pipeline_distributions "
        " WHERE target = ANY($1::text[])",
        list(LEGACY_SITE_TARGETS),
    )
    assert not stranded, [r["target"] for r in stranded]
