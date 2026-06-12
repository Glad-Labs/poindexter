"""Unit tests for the seo_refresh auto-enqueue job (no DB — fake pool)."""

from __future__ import annotations

import pytest


def test_job_has_required_attrs():
    from services.jobs.enqueue_seo_refreshes import EnqueueSeoRefreshesJob

    job = EnqueueSeoRefreshesJob()
    assert job.name == "enqueue_seo_refreshes"
    assert isinstance(job.schedule, str) and job.schedule


def test_job_registered_in_core_samples():
    from plugins.registry import get_core_samples

    jobs = get_core_samples().get("jobs", [])
    assert any(getattr(j, "name", None) == "enqueue_seo_refreshes" for j in jobs)


class _SC:
    def __init__(self, vals):
        self._v = vals

    def get_bool(self, key, default):
        return self._v.get(key, default)

    def get_float(self, key, default):
        return self._v.get(key, default)


@pytest.mark.asyncio
async def test_noop_when_refresh_disabled():
    from services.jobs.enqueue_seo_refreshes import EnqueueSeoRefreshesJob

    job = EnqueueSeoRefreshesJob()
    res = await job.run(
        pool=object(), config={"_site_config": _SC({"seo.refresh.enabled": False})}
    )
    assert res.ok is True
    assert "off" in res.detail.lower()


@pytest.mark.asyncio
async def test_noop_when_no_site_config():
    from services.jobs.enqueue_seo_refreshes import EnqueueSeoRefreshesJob

    job = EnqueueSeoRefreshesJob()
    res = await job.run(pool=object(), config={})
    assert res.ok is True


@pytest.mark.asyncio
async def test_enqueues_capped_and_parks(monkeypatch):
    from services.jobs import enqueue_seo_refreshes as mod

    candidates = [
        {
            "opportunity_id": "opp-1",
            "post_id": "post-1",
            "slug": "a",
            "target_query": "",
            "gap_score": 900.0,
        },
        {
            "opportunity_id": "opp-2",
            "post_id": "post-2",
            "slug": "b",
            "target_query": "q",
            "gap_score": 500.0,
        },
    ]
    parked = []

    class _Conn:
        async def fetch(self, sql, *args):
            # max_per_run is passed as $1; the fake echoes all candidates (the
            # real cap is a SQL LIMIT — out of scope for a fake-pool unit test).
            return candidates

        async def execute(self, sql, *args):
            if "UPDATE seo_opportunities" in sql:
                parked.append(args[0])

        async def __aenter__(self):
            return self

        async def __aexit__(self, *a):
            return False

    class _Pool:
        def acquire(self):
            return _Conn()

    added = []

    class _Tasks:
        def __init__(self, pool):
            pass

        async def add_task(self, data):
            added.append(data)
            return f"task-{len(added)}"

    monkeypatch.setattr(mod, "TasksDatabase", _Tasks)
    monkeypatch.setattr(mod, "emit_finding", lambda **k: None)

    job = mod.EnqueueSeoRefreshesJob()
    res = await job.run(
        pool=_Pool(),
        config={
            "_site_config": _SC(
                {"seo.refresh.enabled": True, "seo.refresh.max_per_run": 2}
            )
        },
    )
    assert res.ok is True
    assert res.changes_made == 2
    # Each enqueued task is a seo_refresh with the post id in metadata.
    assert all(d["template_slug"] == "seo_refresh" for d in added)
    assert {d["task_metadata"]["post_id"] for d in added} == {"post-1", "post-2"}
    # Both opportunities parked to queued.
    assert set(parked) == {"opp-1", "opp-2"}
