"""The probe that would have caught seven bugs in two weeks.

Each of those was correct code one hop short of its consumer, reporting
success, raising nothing, with every flag reading correct. The only signal that
separated them from healthy code was the database answering "has this EVER
produced its artifact?" — which is what this probe asks.
"""
from __future__ import annotations

from typing import Any

import pytest

from poindexter.services.jobs.probe_unproductive_capabilities import (
    ProbeUnproductiveCapabilitiesJob,
)


class _Cfg:
    def __init__(self, **vals):
        self._v = vals

    def get(self, key, default=None):
        return self._v.get(key, default)

    def get_bool(self, key, default=False):
        return bool(self._v.get(key, default))


class _Conn:
    def __init__(self, barren, scheduled, plugin_rows):
        self._barren, self._scheduled, self._plugin_rows = barren, scheduled, plugin_rows

    async def __aenter__(self):
        return self

    async def __aexit__(self, *a):
        pass

    async def fetch(self, query, *args):
        if "COALESCE(total_records, 0) = 0" in query:
            return self._barren
        if "DISTINCT tap_type" in query:
            return [{"tap_type": t} for t in self._scheduled]
        if "app_settings" in query:
            return self._plugin_rows
        raise AssertionError(f"unexpected query: {query[:60]}")


class _Pool:
    def __init__(self, barren=None, scheduled=(), plugin_rows=None):
        self._barren = barren or []
        self._scheduled = scheduled
        self._plugin_rows = plugin_rows or []

    def acquire(self):
        return _Conn(self._barren, self._scheduled, self._plugin_rows)


def _tap(name, runs=187, tap_type="gsc_query_gap", status="success"):
    return {
        "name": name, "tap_type": tap_type, "handler_name": "builtin_topic_source",
        "total_runs": runs, "last_run_status": status, "last_run_at": None,
    }


@pytest.fixture
def emitted(monkeypatch):
    calls: list[dict[str, Any]] = []
    monkeypatch.setattr(
        "poindexter.services.jobs.probe_unproductive_capabilities.emit_finding",
        lambda **kw: calls.append(kw),
    )
    return calls


@pytest.fixture
def no_sources(monkeypatch):
    monkeypatch.setattr(
        "poindexter.plugins.registry.get_topic_sources", lambda: [], raising=False
    )


class _Src:
    def __init__(self, name):
        self.name = name


@pytest.mark.asyncio
class TestBarrenTaps:
    """A tap that runs and never produces is the gsc_query_gap shape."""

    async def test_a_tap_with_runs_and_no_records_is_reported(self, emitted, no_sources):
        job = ProbeUnproductiveCapabilitiesJob()
        res = await job.run(_Pool(barren=[_tap("glad-labs_gsc_query_gap")]), {"_site_config": _Cfg()})
        assert res.ok
        assert len(emitted) == 1
        assert "glad-labs_gsc_query_gap" in emitted[0]["extra"]["barren_taps"]

    async def test_reporting_success_does_not_exempt_it(self, emitted, no_sources):
        """187 runs, 0 records, `success` every time — that IS the failure mode."""
        job = ProbeUnproductiveCapabilitiesJob()
        await job.run(
            _Pool(barren=[_tap("t", runs=187, status="success")]),
            {"_site_config": _Cfg()},
        )
        assert emitted, "a green status must not suppress the finding"

    async def test_nothing_barren_emits_nothing(self, emitted, no_sources):
        job = ProbeUnproductiveCapabilitiesJob()
        res = await job.run(_Pool(), {"_site_config": _Cfg()})
        assert res.ok
        assert emitted == []


@pytest.mark.asyncio
class TestUnscheduledSources:
    """A plugin row PERMITS a source; an external_taps row SCHEDULES it."""

    async def test_registered_source_with_no_tap_row_is_reported(self, emitted, monkeypatch):
        monkeypatch.setattr(
            "poindexter.plugins.registry.get_topic_sources",
            lambda: [_Src("hackernews"), _Src("codebase")],
            raising=False,
        )
        job = ProbeUnproductiveCapabilitiesJob()
        await job.run(_Pool(scheduled=("hackernews",)), {"_site_config": _Cfg()})
        assert emitted[0]["extra"]["unscheduled_sources"] == ["codebase"]

    async def test_an_explicit_enabled_false_is_a_deliberate_no(self, emitted, monkeypatch):
        """igdb ships `enabled: false` — a stated choice, never a gap."""
        monkeypatch.setattr(
            "poindexter.plugins.registry.get_topic_sources",
            lambda: [_Src("igdb")],
            raising=False,
        )
        job = ProbeUnproductiveCapabilitiesJob()
        res = await job.run(
            _Pool(plugin_rows=[{"key": "plugin.topic_source.igdb", "value": '{"enabled": false}'}]),
            {"_site_config": _Cfg()},
        )
        assert emitted == [], "an explicit opt-out must not be reported"
        assert res.ok

    async def test_a_missing_plugin_row_still_counts_as_permitted(self, emitted, monkeypatch):
        """runner.py defaults a MISSING row to enabled, so absence is not a no."""
        monkeypatch.setattr(
            "poindexter.plugins.registry.get_topic_sources",
            lambda: [_Src("benchmark_findings")],
            raising=False,
        )
        job = ProbeUnproductiveCapabilitiesJob()
        await job.run(_Pool(plugin_rows=[]), {"_site_config": _Cfg()})
        assert emitted[0]["extra"]["unscheduled_sources"] == ["benchmark_findings"]

    async def test_unparseable_plugin_json_does_not_suppress(self, emitted, monkeypatch):
        """A malformed value must never silently hide a real gap."""
        monkeypatch.setattr(
            "poindexter.plugins.registry.get_topic_sources",
            lambda: [_Src("codebase")],
            raising=False,
        )
        job = ProbeUnproductiveCapabilitiesJob()
        await job.run(
            _Pool(plugin_rows=[{"key": "plugin.topic_source.codebase", "value": "{not json"}]),
            {"_site_config": _Cfg()},
        )
        assert emitted[0]["extra"]["unscheduled_sources"] == ["codebase"]


@pytest.mark.asyncio
class TestRoutingAndGating:
    async def test_severity_is_warn_so_the_finding_can_actually_route(self, emitted, no_sources):
        """findings_alert_router fetches only warn/warning/critical BEFORE the
        per-kind min_severity policy is read, so `info` can never reach Discord."""
        job = ProbeUnproductiveCapabilitiesJob()
        await job.run(_Pool(barren=[_tap("t")]), {"_site_config": _Cfg()})
        assert emitted[0]["severity"] == "warn"

    async def test_dedup_key_is_stable(self, emitted, no_sources):
        job = ProbeUnproductiveCapabilitiesJob()
        await job.run(_Pool(barren=[_tap("t")]), {"_site_config": _Cfg()})
        await job.run(_Pool(barren=[_tap("other")]), {"_site_config": _Cfg()})
        assert emitted[0]["dedup_key"] == emitted[1]["dedup_key"]

    async def test_the_probe_can_be_switched_off(self, emitted, no_sources):
        job = ProbeUnproductiveCapabilitiesJob()
        res = await job.run(
            _Pool(barren=[_tap("t")]),
            {"_site_config": _Cfg(unproductive_capabilities_probe_enabled=False)},
        )
        assert res.ok
        assert emitted == []

    async def test_run_floor_is_configurable(self, emitted, no_sources):
        job = ProbeUnproductiveCapabilitiesJob()
        await job.run(
            _Pool(barren=[_tap("t")]),
            {"_site_config": _Cfg(unproductive_capabilities_min_runs=5)},
        )
        assert emitted[0]["extra"]["min_runs"] == 5
