"""Unit tests for ``PodcastDistributeJob`` (#689 deviation, Stage-3 distribution).

The podcast twin of ``media_distribute``: link task-keyed podcast assets to their
published post, seed ``media_approvals(medium='podcast')`` via ``record_pending``,
and (on approval) upload to R2 + rebuild the RSS feed.

Critically, the seed pass also heals the **backlog**: podcast assets the
reconciliation watchdog already wrote (``post_id`` set, no approval row) get
seeded — this is the fix for the 2026-05-28 Spotify freeze.
"""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock, patch

import pytest

from services.jobs import podcast_distribute
from services.jobs.podcast_distribute import PodcastDistributeJob
from services.site_config import SiteConfig


class _FakePool:
    def __init__(
        self,
        *,
        unlinked: list[dict[str, Any]] | None = None,
        unapproved: list[dict[str, Any]] | None = None,
        approved: list[dict[str, Any]] | None = None,
        resolve: Any = None,
        existing: Any = None,
    ) -> None:
        self.unlinked = unlinked or []
        self.unapproved = unapproved or []
        self.approved = approved or []
        self.resolve = resolve
        # Return value of the Pass-1 link-guard existing-podcast check
        # (SELECT 1 FROM media_assets ...). Default None = post has no podcast
        # asset yet, so the render links normally.
        self.existing = existing
        self.executed: list[tuple[str, tuple[Any, ...]]] = []

    async def fetch(self, sql: str, *args: Any) -> list[dict[str, Any]]:
        if "post_id IS NULL" in sql:
            return self.unlinked
        if "NOT EXISTS" in sql:
            return self.unapproved
        if "status = 'approved'" in sql:
            return self.approved
        return []

    async def fetchval(self, sql: str, *args: Any) -> Any:
        # The link-guard existing-podcast check reads media_assets; the post
        # resolve reads posts. Dispatch so the two return distinct fixtures.
        if "FROM media_assets" in sql:
            return self.existing
        return self.resolve

    async def execute(self, sql: str, *args: Any) -> str:
        self.executed.append((sql, args))
        return "UPDATE 1"


def _cfg(enabled: bool = True) -> dict[str, Any]:
    sc = SiteConfig(
        initial_config={"podcast_pipeline_trigger_enabled": "true" if enabled else "false"}
    )
    return {"_site_config": sc}


@pytest.mark.asyncio
async def test_dormant_when_flag_off() -> None:
    res = await PodcastDistributeJob().run(_FakePool(unapproved=[{"post_id": "p1"}]), _cfg(False))
    assert res.changes_made == 0


@pytest.mark.asyncio
async def test_seed_heals_backlog_of_linked_unapproved_assets() -> None:
    # A podcast asset the watchdog already linked (post_id set) but never seeded.
    pool = _FakePool(unapproved=[{"post_id": "p1"}, {"post_id": "p2"}])
    with patch.object(podcast_distribute, "record_pending", new=AsyncMock()) as rp:
        res = await PodcastDistributeJob().run(pool, _cfg(True))

    seeded = {call.args[1] for call in rp.await_args_list}
    assert seeded == {"p1", "p2"}
    assert res.changes_made >= 2


@pytest.mark.asyncio
async def test_links_unlinked_asset_to_resolved_post() -> None:
    pool = _FakePool(
        unlinked=[{"id": "a1", "task_id": "t1", "type": "podcast"}],
        resolve="p1",
    )
    with patch.object(podcast_distribute, "record_pending", new=AsyncMock()):
        await PodcastDistributeJob().run(pool, _cfg(True))

    # The link UPDATE ran with the resolved post id + asset id.
    link_calls = [a for (sql, a) in pool.executed if "post_id = $1" in sql]
    assert ("p1", "a1") in link_calls


@pytest.mark.asyncio
async def test_seed_threads_storage_path_to_quality_eval() -> None:
    """Both seed passes thread the asset's storage_path to record_pending
    as file_path so the Layer-1 quality eval can probe it (poindexter#816);
    a missing/empty path degrades to None."""
    pool = _FakePool(
        unlinked=[{"id": "a1", "task_id": "t1", "type": "podcast",
                   "storage_path": "/data/media/ep1.mp3"}],
        unapproved=[{"post_id": "p2", "storage_path": ""}],
        resolve="p1",
    )
    with patch.object(podcast_distribute, "record_pending", new=AsyncMock()) as rp:
        await PodcastDistributeJob().run(pool, _cfg(True))

    by_post = {c.args[1]: c.kwargs.get("file_path") for c in rp.await_args_list}
    assert by_post == {"p1": "/data/media/ep1.mp3", "p2": None}
    # Both queries must actually select the column the row shape relies on.
    assert "storage_path" in podcast_distribute._UNLINKED_PODCAST_SQL
    assert "storage_path" in podcast_distribute._UNAPPROVED_LINKED_SQL


@pytest.mark.asyncio
async def test_unlinked_asset_left_when_post_unresolved() -> None:
    pool = _FakePool(
        unlinked=[{"id": "a1", "task_id": "t1", "type": "podcast"}],
        resolve=None,  # task not published yet
    )
    with patch.object(podcast_distribute, "record_pending", new=AsyncMock()):
        await PodcastDistributeJob().run(pool, _cfg(True))
    link_calls = [a for (sql, a) in pool.executed if "post_id = $1" in sql]
    assert link_calls == []


# --- #884: link-guard prune + newest-render delivery (media_distribute parity) ---


@pytest.mark.asyncio
async def test_pass1_prunes_orphan_when_post_already_has_podcast() -> None:
    """A task-keyed render whose post already holds a podcast asset is pruned —
    not linked — so the link UPDATE never violates uniq_media_assets_post_podcast_type
    and the next cycle doesn't rediscover the orphan (mirrors media_distribute)."""
    pool = _FakePool(
        unlinked=[{"id": "a1", "task_id": "t1", "type": "podcast",
                   "storage_path": "/dup.mp3"}],
        resolve="p1",
        existing=1,  # the post already has a podcast asset
    )
    with patch.object(podcast_distribute, "record_pending", new=AsyncMock()) as rp, \
         patch.object(podcast_distribute, "emit_finding") as ef:
        await PodcastDistributeJob().run(pool, _cfg(True))

    # The orphan row was pruned, and it was NOT linked or seeded for approval.
    prune_calls = [a for (sql, a) in pool.executed if "DELETE FROM media_assets" in sql]
    link_calls = [a for (sql, a) in pool.executed if "SET post_id = $1" in sql]
    assert ("a1",) in prune_calls
    assert link_calls == []
    rp.assert_not_awaited()
    # A duplicate finding is emitted for observability (self-heal, not silent).
    assert ef.call_count == 1
    assert ef.call_args.kwargs.get("kind") == "duplicate_podcast_asset"


@pytest.mark.asyncio
async def test_pass1_links_when_no_existing_podcast() -> None:
    """The guard is a no-op when the post has no podcast asset yet — the
    task-keyed render links + seeds exactly as before (regression guard)."""
    pool = _FakePool(
        unlinked=[{"id": "a1", "task_id": "t1", "type": "podcast"}],
        resolve="p1",
        existing=None,  # no existing podcast asset for the post
    )
    with patch.object(podcast_distribute, "record_pending", new=AsyncMock()) as rp:
        await PodcastDistributeJob().run(pool, _cfg(True))

    link_calls = [a for (sql, a) in pool.executed if "SET post_id = $1" in sql]
    prune_calls = [a for (sql, a) in pool.executed if "DELETE FROM media_assets" in sql]
    assert ("p1", "a1") in link_calls
    assert prune_calls == []
    rp.assert_awaited()


def test_approved_undispatched_query_picks_newest_render() -> None:
    """Delivery collapses to one row per post (newest render) so a post with
    duplicate podcast rows never uploads a stale render to the public feed (#884).
    Mirrors the DISTINCT ON pattern already used by _UNAPPROVED_LINKED_SQL."""
    sql = podcast_distribute._APPROVED_UNDISPATCHED_SQL
    assert "DISTINCT ON (ma.post_id)" in sql
    assert "created_at DESC" in sql
