"""Scheduler job-liveness seam — every job run is bracketed by a live_activity
row (unless the job opts out with activity_silent)."""
import asyncio

import pytest

from plugins.job import JobResult
from plugins.scheduler import PluginScheduler

pytestmark = pytest.mark.asyncio


class _Job:
    name = "demo"
    description = "Demo"
    schedule = "every 5 minutes"
    idempotent = True

    async def run(self, pool, cfg):
        return JobResult(ok=True, detail="ok", changes_made=0)


async def test_job_run_brackets_activity(monkeypatch):
    calls = []

    async def fake_begin(pool, **kw):
        calls.append(("begin", kw))
        return 7

    async def fake_finish(pool, aid, **kw):
        calls.append(("finish", aid, kw))

    monkeypatch.setattr("plugins.scheduler.live_activity.begin", fake_begin)
    monkeypatch.setattr("plugins.scheduler.live_activity.finish", fake_finish)

    await PluginScheduler._invoke_job_with_activity(pool=None, job=_Job(), cfg={})
    assert calls[0][0] == "begin" and calls[0][1]["kind"] == "job"
    assert calls[-1][0] == "finish" and calls[-1][1] == 7 and calls[-1][2]["status"] == "ok"


async def test_activity_silent_job_skips(monkeypatch):
    calls = []
    monkeypatch.setattr(
        "plugins.scheduler.live_activity.begin",
        lambda *a, **k: calls.append("begin"),
    )
    job = _Job()
    job.activity_silent = True
    await PluginScheduler._invoke_job_with_activity(pool=None, job=job, cfg={})
    assert calls == []


class _SiteConfigStub:
    """Minimal SiteConfig stand-in exposing a tiny heartbeat interval so a
    long job beats several times within a short test window."""

    def get(self, key, default=None):
        return "0.01" if key == "live_activity_heartbeat_seconds" else default


async def test_long_running_job_gets_heartbeat(monkeypatch):
    """A job that runs longer than the heartbeat interval keeps its row fresh:
    update() fires at least once DURING the run, so get_live_activity's
    freshness window won't hide a still-running long job. This is the fix for
    the pulse-goes-blank bug — a live LLM topic sweep vanished after 120s with
    no heartbeat while it was still burning the GPU."""
    beats = []
    finishes = []

    async def fake_begin(pool, **kw):
        return 9

    async def fake_update(pool, aid, **kw):
        beats.append(aid)

    async def fake_finish(pool, aid, **kw):
        finishes.append((aid, kw.get("status")))

    monkeypatch.setattr("plugins.scheduler.live_activity.begin", fake_begin)
    monkeypatch.setattr("plugins.scheduler.live_activity.update", fake_update)
    monkeypatch.setattr("plugins.scheduler.live_activity.finish", fake_finish)

    class _SlowJob:
        name = "slow"
        description = "Slow sweep"

        async def run(self, pool, cfg):
            await asyncio.sleep(0.05)
            return JobResult(ok=True, detail="ok", changes_made=0)

    result = await PluginScheduler._invoke_job_with_activity(
        pool=None, job=_SlowJob(), cfg={"_site_config": _SiteConfigStub()}
    )
    assert result.ok
    assert len(beats) >= 1 and set(beats) == {9}, "heartbeat should fire during the run"
    assert finishes == [(9, "ok")]


async def test_heartbeat_cancelled_when_job_raises(monkeypatch):
    """The exception path still finishes 'fail' and tears down the heartbeat
    sidecar — a job that raises must not leak a live-forever heartbeat task."""
    finishes = []

    async def fake_begin(pool, **kw):
        return 3

    async def fake_update(pool, aid, **kw):
        pass

    async def fake_finish(pool, aid, **kw):
        finishes.append((aid, kw.get("status")))

    monkeypatch.setattr("plugins.scheduler.live_activity.begin", fake_begin)
    monkeypatch.setattr("plugins.scheduler.live_activity.update", fake_update)
    monkeypatch.setattr("plugins.scheduler.live_activity.finish", fake_finish)

    class _BoomJob:
        name = "boom"
        description = "Boom"

        async def run(self, pool, cfg):
            raise RuntimeError("boom")

    with pytest.raises(RuntimeError):
        await PluginScheduler._invoke_job_with_activity(
            pool=None, job=_BoomJob(), cfg={}
        )
    assert finishes == [(3, "fail")]
