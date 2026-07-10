"""Scheduler job-liveness seam — every job run is bracketed by a live_activity
row (unless the job opts out with activity_silent)."""
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
