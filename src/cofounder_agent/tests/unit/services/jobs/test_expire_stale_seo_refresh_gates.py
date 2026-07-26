"""Unit tests for the seo_refresh gate-expiry sweep (no DB — fake pool).

The sweep dismisses seo_refresh tasks parked at seo_refresh_gate past
``seo.refresh.gate_max_parked_days`` through ``approval_service.reject`` with
the automation kwargs (actor='staleness_sweep', status_override='dismissed',
record_outcome=False) so expiry is queue hygiene, never learning signal.
"""

from __future__ import annotations

from datetime import timedelta

import pytest


class _SC:
    def __init__(self, vals):
        self._v = vals

    def get_bool(self, key, default):
        return self._v.get(key, default)

    def get_float(self, key, default):
        return self._v.get(key, default)


def test_job_has_required_attrs():
    from services.jobs.expire_stale_seo_refresh_gates import (
        ExpireStaleSeoRefreshGatesJob,
    )

    job = ExpireStaleSeoRefreshGatesJob()
    assert job.name == "expire_stale_seo_refresh_gates"
    assert isinstance(job.schedule, str) and job.schedule
    assert job.idempotent is False


def test_job_registered_in_core_samples():
    from plugins.registry import get_core_samples

    jobs = get_core_samples().get("jobs", [])
    assert any(
        getattr(j, "name", None) == "expire_stale_seo_refresh_gates" for j in jobs
    )


def test_max_parked_days_seeded_in_defaults():
    from services.settings_defaults import DEFAULTS

    assert DEFAULTS.get("seo.refresh.gate_max_parked_days") == "14"


@pytest.mark.asyncio
async def test_disabled_when_max_days_zero():
    from services.jobs.expire_stale_seo_refresh_gates import (
        ExpireStaleSeoRefreshGatesJob,
    )

    job = ExpireStaleSeoRefreshGatesJob()
    res = await job.run(
        pool=object(),
        config={"_site_config": _SC({"seo.refresh.gate_max_parked_days": 0})},
    )
    assert res.ok is True
    assert "disabled" in res.detail.lower()


@pytest.mark.asyncio
async def test_defaults_to_14_days_without_site_config(monkeypatch):
    """No site_config → the code default (14d) applies, sweep still runs."""
    from services.jobs import expire_stale_seo_refresh_gates as mod

    seen_secs = []

    class _Conn:
        async def fetch(self, sql, *args):
            seen_secs.append(args[0])
            return []

        async def __aenter__(self):
            return self

        async def __aexit__(self, *a):
            return False

    class _Pool:
        def acquire(self):
            return _Conn()

    job = mod.ExpireStaleSeoRefreshGatesJob()
    res = await job.run(pool=_Pool(), config={})
    assert res.ok is True
    assert seen_secs == [14 * 86400.0]


@pytest.mark.asyncio
async def test_expires_stale_rows_with_automation_kwargs(monkeypatch):
    from services.jobs import expire_stale_seo_refresh_gates as mod

    stale_rows = [
        {
            "task_id": "t-old-1",
            "topic": "slug-one",
            "gate_paused_at": None,
            "parked_for": timedelta(days=16),
        },
        {
            "task_id": "t-old-2",
            "topic": "slug-two",
            "gate_paused_at": None,
            "parked_for": timedelta(days=15),
        },
    ]

    class _Conn:
        async def fetch(self, sql, *args):
            assert "awaiting_gate = 'seo_refresh_gate'" in sql
            return stale_rows

        async def __aenter__(self):
            return self

        async def __aexit__(self, *a):
            return False

    class _Pool:
        def acquire(self):
            return _Conn()

    reject_calls = []

    async def _fake_reject(**kwargs):
        reject_calls.append(kwargs)
        return {"ok": True, "new_status": "dismissed"}

    findings = []
    monkeypatch.setattr(mod, "reject_gate", _fake_reject)
    monkeypatch.setattr(
        mod, "emit_finding", lambda **kw: findings.append(kw)
    )

    job = mod.ExpireStaleSeoRefreshGatesJob()
    res = await job.run(
        pool=_Pool(),
        config={"_site_config": _SC({"seo.refresh.gate_max_parked_days": 14})},
    )

    assert res.ok is True
    assert res.changes_made == 2
    assert res.metrics == {"expired": 2}
    assert len(reject_calls) == 2
    for call in reject_calls:
        # The automation contract: never a human-shaped reject.
        assert call["actor"] == "staleness_sweep"
        assert call["status_override"] == "dismissed"
        assert call["record_outcome"] is False
        assert call["gate_name"] == "seo_refresh_gate"
        assert "auto-expired" in call["reason"]
    # One routable (warn) finding summarizing the batch.
    assert len(findings) == 1
    assert findings[0]["kind"] == "seo_refresh_gate_expired"
    assert findings[0]["severity"] == "warn"


@pytest.mark.asyncio
async def test_one_contested_row_never_aborts_the_run(monkeypatch):
    """A row approved mid-sweep raises TaskNotPausedError — skip, keep going."""
    from services.approval_service import TaskNotPausedError
    from services.jobs import expire_stale_seo_refresh_gates as mod

    stale_rows = [
        {"task_id": "t-contested", "topic": "a", "gate_paused_at": None,
         "parked_for": timedelta(days=20)},
        {"task_id": "t-ok", "topic": "b", "gate_paused_at": None,
         "parked_for": timedelta(days=20)},
    ]

    class _Conn:
        async def fetch(self, sql, *args):
            return stale_rows

        async def __aenter__(self):
            return self

        async def __aexit__(self, *a):
            return False

    class _Pool:
        def acquire(self):
            return _Conn()

    async def _fake_reject(**kwargs):
        if kwargs["task_id"] == "t-contested":
            raise TaskNotPausedError("approved mid-sweep")
        return {"ok": True}

    monkeypatch.setattr(mod, "reject_gate", _fake_reject)
    monkeypatch.setattr(mod, "emit_finding", lambda **kw: None)

    job = mod.ExpireStaleSeoRefreshGatesJob()
    res = await job.run(
        pool=_Pool(),
        config={"_site_config": _SC({"seo.refresh.gate_max_parked_days": 14})},
    )
    assert res.ok is True
    assert res.metrics == {"expired": 1}


@pytest.mark.asyncio
async def test_no_stale_rows_emits_no_finding(monkeypatch):
    from services.jobs import expire_stale_seo_refresh_gates as mod

    class _Conn:
        async def fetch(self, sql, *args):
            return []

        async def __aenter__(self):
            return self

        async def __aexit__(self, *a):
            return False

    class _Pool:
        def acquire(self):
            return _Conn()

    findings = []
    monkeypatch.setattr(mod, "emit_finding", lambda **kw: findings.append(kw))

    job = mod.ExpireStaleSeoRefreshGatesJob()
    res = await job.run(
        pool=_Pool(),
        config={"_site_config": _SC({"seo.refresh.gate_max_parked_days": 14})},
    )
    assert res.ok is True
    assert res.metrics == {"expired": 0}
    assert findings == []
