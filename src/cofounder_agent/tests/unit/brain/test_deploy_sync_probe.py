"""Unit tests for ``brain/deploy_sync_probe.py`` (poindexter#977).

Pins the deploy-path dead-man's switch: a ``deploy_sync_run`` heartbeat that
goes stale means the deploy path stopped RUNNING (critical — merged main is
silently not shipping), while an unbroken streak of ``result='error'`` means
it is running and cannot finish (warning — the timer retries on its own).

The failure this guards against produces *no output at all*, so the tests
that matter most are the negative ones: a probe that pages on a healthy
deferral, or that reports green when it has never seen a heartbeat, would be
worse than no probe.
"""

from __future__ import annotations

import json
from unittest.mock import AsyncMock, MagicMock

import pytest

from poindexter.brain import deploy_sync_probe
from poindexter.brain.deploy_sync_probe import (
    DEFAULT_ERROR_STREAK,
    DEFAULT_MAX_AGE_MINUTES,
    FALLBACK_DRIVER,
    FINDING_KIND_FAILING,
    FINDING_KIND_FALLBACK,
    FINDING_KIND_STALE,
    HEARTBEAT_EVENT,
    run_deploy_sync_probe,
)
from tests.unit._nonempty import nonempty


def _row(*, age_minutes: float, result: str = "deployed", detail: str = "",
         driver: str | None = None, driver_commit: str = ""):
    details = {"result": result, "head": "abc1234", "detail": detail, "host": "pop-os"}
    if driver is not None:  # heartbeats from before the launcher carry no driver
        details["driver"] = driver
        details["driver_commit"] = driver_commit
    return {
        "timestamp": None,
        "age_minutes": age_minutes,
        "details": json.dumps(details),
    }


def _pool(*, rows=None, enabled="true", settings=None):
    """asyncpg pool stub: settings via fetchval, heartbeats via fetch."""
    pool = MagicMock()
    cfg = {"deploy_sync_probe_enabled": enabled, **(settings or {})}

    async def _fetchval(sql, *args):
        if "app_settings" in sql:
            return cfg.get(args[0])
        return None

    async def _fetch(sql, *args):  # noqa: ARG001
        return list(rows or [])

    pool.fetchval = AsyncMock(side_effect=_fetchval)
    pool.fetch = AsyncMock(side_effect=_fetch)
    pool.execute = AsyncMock(return_value=None)
    return pool


def _findings(pool):
    out = []
    for call in pool.execute.call_args_list:
        sql = call.args[0]
        if "'finding'" in sql:
            out.append((json.loads(call.args[1]), call.args[2]))
    return out


@pytest.mark.asyncio
async def test_fresh_successful_run_is_quiet():
    pool = _pool(rows=[_row(age_minutes=4.0)])
    summary = await run_deploy_sync_probe(pool)
    assert summary["ok"] is True
    assert summary["status"] == "fresh"
    assert _findings(pool) == []


@pytest.mark.asyncio
async def test_stale_heartbeat_pages_critical():
    """The 2026-08-02 case: the timer stopped scheduling and merged main sat
    undeployed for 45 minutes with no signal."""
    pool = _pool(rows=[_row(age_minutes=45.0)])
    summary = await run_deploy_sync_probe(pool)
    assert summary["ok"] is False
    assert summary["status"] == "stale"
    (finding, severity), = _findings(pool)
    assert severity == "critical"
    assert finding["kind"] == FINDING_KIND_STALE
    # The remediation is the non-obvious part — a `NEXT: -` timer needs a
    # manual start, which is not something an operator guesses at 00:01.
    assert "systemctl" in finding["body"]
    assert finding["extra"]["age_minutes"] == 45.0


@pytest.mark.asyncio
async def test_age_threshold_is_configurable_and_respected():
    rows = [_row(age_minutes=20.0)]
    assert (await run_deploy_sync_probe(_pool(rows=rows)))["status"] == "fresh"
    tight = _pool(rows=rows, settings={"deploy_sync_max_age_minutes": "15"})
    assert (await run_deploy_sync_probe(tight))["status"] == "stale"


@pytest.mark.asyncio
async def test_unbroken_error_streak_warns():
    rows = [_row(age_minutes=2.0, result="error", detail="git fetch failed")] * 3
    pool = _pool(rows=rows)
    summary = await run_deploy_sync_probe(pool)
    assert summary["ok"] is False
    assert summary["status"] == "failing"
    (finding, severity), = _findings(pool)
    assert severity == "warning"
    assert finding["kind"] == FINDING_KIND_FAILING
    assert "git fetch failed" in finding["body"]


@pytest.mark.asyncio
async def test_a_single_error_between_good_runs_is_the_retry_working():
    """One bad run is the mechanism succeeding, not failing. Paging on it
    would make a transient DNS blip indistinguishable from a broken deploy."""
    rows = [
        _row(age_minutes=2.0, result="deployed"),
        _row(age_minutes=12.0, result="error"),
        _row(age_minutes=22.0, result="deployed"),
    ]
    pool = _pool(rows=rows)
    summary = await run_deploy_sync_probe(pool)
    assert summary["ok"] is True
    assert _findings(pool) == []


@pytest.mark.asyncio
async def test_deferred_run_counts_as_healthy_liveness():
    """`deferred-active-flow` means the sync waited out an in-flight render
    rather than restarting a busy worker — the mechanism working as designed.
    Counting it as an error would page every busy evening."""
    rows = [_row(age_minutes=3.0, result="deferred-active-flow")] * 3
    pool = _pool(rows=rows)
    summary = await run_deploy_sync_probe(pool)
    assert summary["ok"] is True
    assert _findings(pool) == []


@pytest.mark.asyncio
async def test_short_error_history_does_not_page():
    """Two errors when the threshold is three is not yet a streak."""
    rows = [_row(age_minutes=2.0, result="error")] * 2
    pool = _pool(rows=rows)
    summary = await run_deploy_sync_probe(pool)
    assert summary["ok"] is True
    assert _findings(pool) == []


@pytest.mark.asyncio
async def test_no_history_is_reported_but_never_pages():
    """A host that never installed the deploy-sync timer is indistinguishable
    from one whose timer never fired. The first is a legitimate config, so
    this reports rather than pages — but it must not read as 'fresh'."""
    pool = _pool(rows=[])
    summary = await run_deploy_sync_probe(pool)
    assert summary["ok"] is True
    assert summary["status"] == "no_history"
    assert summary["status"] != "fresh"
    assert _findings(pool) == []


@pytest.mark.asyncio
async def test_disabled_probe_is_inert():
    pool = _pool(rows=[_row(age_minutes=999.0)], enabled="false")
    summary = await run_deploy_sync_probe(pool)
    assert summary["status"] == "disabled"
    assert _findings(pool) == []


@pytest.mark.asyncio
async def test_query_failure_degrades_without_a_false_page():
    """A probe that cannot read must not claim the deploy path is broken."""
    pool = _pool(rows=[])
    pool.fetch = AsyncMock(side_effect=RuntimeError("connection reset"))
    summary = await run_deploy_sync_probe(pool)
    assert summary["ok"] is True
    assert summary["status"] == "query_failed"
    assert _findings(pool) == []


@pytest.mark.asyncio
async def test_no_pool_is_survivable():
    summary = await run_deploy_sync_probe(None)
    assert summary["ok"] is True
    assert summary["status"] == "no_pool"


def _finding_kinds() -> list[str]:
    """Every FINDING_KIND_* the probe declares — derived, never hand-listed, so
    a new kind cannot slip past the checks below."""
    return [v for k, v in vars(deploy_sync_probe).items() if k.startswith("FINDING_KIND_")]


@pytest.mark.asyncio
async def test_findings_use_dot_free_kinds():
    """`findings.<kind>.delivery` is one app_settings key, so a dot in the
    kind would silently split the policy lookup."""
    for kind in nonempty(_finding_kinds(), "FINDING_KIND_*"):
        assert "." not in kind


def test_every_finding_kind_has_a_declared_delivery_policy():
    """`findings.default` is inert, so an undeclared kind routes by the
    dispatcher's default severity matrix rather than a decision."""
    from poindexter.services.settings_defaults import DEFAULTS

    for kind in nonempty(_finding_kinds(), "FINDING_KIND_*"):
        for field in ("delivery", "fallback", "cooldown_minutes", "min_severity"):
            assert f"findings.{kind}.{field}" in DEFAULTS, f"findings.{kind}.{field}"


# ---- the deploy driver fallback (stack#4172) ----------------------------------


@pytest.mark.asyncio
async def test_a_pass_on_the_last_known_good_driver_warns():
    """The fallback pass reports `deployed` like any other; the driver field is
    the only place it shows, so the probe is what makes it visible."""
    note = ("fallback: the merged driver from 1a2b3c4d5 fails bash -n (line 9: syntax error); "
            "this pass ran the last-known-good driver from 9f8e7d6c5")
    pool = _pool(rows=[_row(age_minutes=3.0, detail=note, driver=FALLBACK_DRIVER,
                            driver_commit="9f8e7d6c5b4a")])
    summary = await run_deploy_sync_probe(pool)
    assert summary["ok"] is False
    assert summary["status"] == "fallback"
    (finding, severity), = _findings(pool)
    assert severity == "warning"
    assert finding["kind"] == FINDING_KIND_FALLBACK
    assert "fails bash -n" in finding["body"], "the launcher's reason reaches the operator"
    assert "9f8e7d6c5" in finding["body"]
    assert "still moving" in finding["body"]
    assert "--report" in finding["body"]
    assert finding["extra"]["driver"] == FALLBACK_DRIVER


@pytest.mark.asyncio
@pytest.mark.parametrize("driver", ["merged", "direct", None])
async def test_merged_direct_and_pre_launcher_heartbeats_are_not_fallbacks(driver):
    pool = _pool(rows=[_row(age_minutes=3.0, driver=driver)])
    summary = await run_deploy_sync_probe(pool)
    assert summary["ok"] is True
    assert summary["status"] == "fresh"
    assert _findings(pool) == []


@pytest.mark.asyncio
async def test_a_failed_fallback_pass_says_the_path_is_stalled():
    pool = _pool(rows=[_row(age_minutes=3.0, result="error", driver=FALLBACK_DRIVER)])
    await run_deploy_sync_probe(pool)
    (finding, _), = _findings(pool)
    assert finding["kind"] == FINDING_KIND_FALLBACK
    assert "stalled" in finding["body"]


@pytest.mark.asyncio
async def test_fallback_and_an_error_streak_are_both_reported():
    rows = [_row(age_minutes=2.0, result="error", driver=FALLBACK_DRIVER)] * DEFAULT_ERROR_STREAK
    pool = _pool(rows=rows)
    summary = await run_deploy_sync_probe(pool)
    assert summary["status"] == "failing"
    assert sorted(f["kind"] for f, _ in _findings(pool)) == sorted([FINDING_KIND_FAILING, FINDING_KIND_FALLBACK])


@pytest.mark.asyncio
async def test_a_stale_path_pages_stale_only():
    """Stale is the outage; which copy ran last is beside the point."""
    pool = _pool(rows=[_row(age_minutes=45.0, driver=FALLBACK_DRIVER)])
    summary = await run_deploy_sync_probe(pool)
    assert summary["status"] == "stale"
    assert [f["kind"] for f, _ in _findings(pool)] == [FINDING_KIND_STALE]


def test_defaults_match_the_ten_minute_timer():
    # Three missed fires plus slack for a long rebuild pass.
    assert DEFAULT_MAX_AGE_MINUTES == 35
    assert DEFAULT_ERROR_STREAK == 3
    assert HEARTBEAT_EVENT == "deploy_sync_run"
