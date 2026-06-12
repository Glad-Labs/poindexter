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
    ) -> None:
        self.unlinked = unlinked or []
        self.unapproved = unapproved or []
        self.approved = approved or []
        self.resolve = resolve
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
async def test_unlinked_asset_left_when_post_unresolved() -> None:
    pool = _FakePool(
        unlinked=[{"id": "a1", "task_id": "t1", "type": "podcast"}],
        resolve=None,  # task not published yet
    )
    with patch.object(podcast_distribute, "record_pending", new=AsyncMock()):
        await PodcastDistributeJob().run(pool, _cfg(True))
    link_calls = [a for (sql, a) in pool.executed if "post_id = $1" in sql]
    assert link_calls == []
