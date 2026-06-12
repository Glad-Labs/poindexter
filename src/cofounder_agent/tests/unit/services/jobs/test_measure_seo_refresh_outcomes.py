"""Unit tests for the seo_refresh outcome-measurement job (no DB — fake pool)."""

from __future__ import annotations

import pytest


def test_job_has_required_attrs():
    from services.jobs.measure_seo_refresh_outcomes import MeasureSeoRefreshOutcomesJob

    job = MeasureSeoRefreshOutcomesJob()
    assert job.name == "measure_seo_refresh_outcomes"
    assert isinstance(job.schedule, str) and job.schedule


def test_job_registered_in_core_samples():
    from plugins.registry import get_core_samples

    jobs = get_core_samples().get("jobs", [])
    assert any(
        getattr(j, "name", None) == "measure_seo_refresh_outcomes" for j in jobs
    )


class _SC:
    def __init__(self, vals):
        self._v = vals

    def get_float(self, key, default):
        return self._v.get(key, default)


@pytest.mark.asyncio
async def test_measures_due_rows_and_writes_outcome(monkeypatch):
    from services.jobs import measure_seo_refresh_outcomes as mod

    due = [
        {
            "opportunity_id": "opp-1",
            "post_id": "post-1",
            "slug": "a",
            "baseline_position": 8.0,
            "baseline_ctr": 0.001,
            "refreshed_at": None,
        }
    ]
    perf = {"impressions": 1000, "clicks": 30, "position": 5.0}
    writes = []

    class _Conn:
        async def fetch(self, sql, *args):
            return due

        async def fetchrow(self, sql, *args):
            return perf

        async def execute(self, sql, *args):
            if "UPDATE seo_opportunities" in sql:
                writes.append(args)

        async def __aenter__(self):
            return self

        async def __aexit__(self, *a):
            return False

    class _Pool:
        def acquire(self):
            return _Conn()

    monkeypatch.setattr(mod, "emit_finding", lambda **k: None)

    job = mod.MeasureSeoRefreshOutcomesJob()
    res = await job.run(
        pool=_Pool(),
        config={"_site_config": _SC({"seo.refresh.outcome_measure_after_days": 14})},
    )
    assert res.ok is True
    assert res.changes_made == 1
    # outcome_position = 5.0, outcome_ctr = 30/1000 = 0.03
    assert writes and writes[0][1] == 5.0
    assert abs(writes[0][2] - 0.03) < 1e-9


@pytest.mark.asyncio
async def test_skips_post_with_no_perf_snapshot(monkeypatch):
    from services.jobs import measure_seo_refresh_outcomes as mod

    due = [
        {
            "opportunity_id": "opp-1",
            "post_id": "post-1",
            "slug": "a",
            "baseline_position": 8.0,
            "baseline_ctr": 0.001,
            "refreshed_at": None,
        }
    ]

    class _Conn:
        async def fetch(self, sql, *args):
            return due

        async def fetchrow(self, sql, *args):
            return None  # no snapshot

        async def execute(self, sql, *args):
            raise AssertionError("must not write outcome with no snapshot")

        async def __aenter__(self):
            return self

        async def __aexit__(self, *a):
            return False

    class _Pool:
        def acquire(self):
            return _Conn()

    monkeypatch.setattr(mod, "emit_finding", lambda **k: None)
    job = mod.MeasureSeoRefreshOutcomesJob()
    res = await job.run(pool=_Pool(), config={"_site_config": _SC({})})
    assert res.ok is True
    assert res.changes_made == 0
