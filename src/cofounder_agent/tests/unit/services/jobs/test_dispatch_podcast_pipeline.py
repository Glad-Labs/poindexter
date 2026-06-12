"""Unit tests for ``DispatchPodcastPipelineJob`` (#689 deviation, Stage-3 trigger).

Mirrors ``DispatchMediaPipelineJob``: claim-before-run on a per-medium
``podcast_dispatched_at`` marker, gated on ``podcast_pipeline_trigger_enabled``.
"""

from __future__ import annotations

from typing import Any
from unittest.mock import patch

import pytest

from services.jobs import dispatch_podcast_pipeline
from services.jobs.dispatch_podcast_pipeline import DispatchPodcastPipelineJob
from services.site_config import SiteConfig


class _FakePool:
    def __init__(self, rows: list[dict[str, Any]], claim_result: str = "UPDATE 1") -> None:
        self.rows = rows
        self.claim_result = claim_result
        self.claimed: list[Any] = []

    async def fetch(self, sql: str, *args: Any) -> list[dict[str, Any]]:
        return self.rows

    async def execute(self, sql: str, *args: Any) -> str:
        self.claimed.append(args[0])
        return self.claim_result


def _cfg(enabled: bool) -> dict[str, Any]:
    sc = SiteConfig(
        initial_config={"podcast_pipeline_trigger_enabled": "true" if enabled else "false"}
    )
    return {"_site_config": sc}


@pytest.mark.asyncio
async def test_dormant_when_flag_off() -> None:
    job = DispatchPodcastPipelineJob()
    res = await job.run(_FakePool([{"task_id": "t1"}]), _cfg(enabled=False))
    assert res.ok is True
    assert res.changes_made == 0


@pytest.mark.asyncio
async def test_dispatches_eligible_tasks() -> None:
    job = DispatchPodcastPipelineJob()
    pool = _FakePool([{"task_id": "t1"}, {"task_id": "t2"}])
    calls: list[str] = []

    async def fake_run(pool_: Any, sc: Any, task_id: str) -> None:
        calls.append(task_id)

    with patch.object(dispatch_podcast_pipeline, "_run_podcast_pipeline", fake_run):
        res = await job.run(pool, _cfg(enabled=True))

    assert res.changes_made == 2
    assert calls == ["t1", "t2"]


@pytest.mark.asyncio
async def test_skips_when_claim_lost() -> None:
    job = DispatchPodcastPipelineJob()
    pool = _FakePool([{"task_id": "t1"}], claim_result="UPDATE 0")
    calls: list[str] = []

    async def fake_run(pool_: Any, sc: Any, task_id: str) -> None:
        calls.append(task_id)

    with patch.object(dispatch_podcast_pipeline, "_run_podcast_pipeline", fake_run):
        res = await job.run(pool, _cfg(enabled=True))

    assert res.changes_made == 0
    assert calls == []
