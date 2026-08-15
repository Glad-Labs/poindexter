"""Overlap-skip visibility in PluginScheduler (2026-08-15 tap incident).

Two contracts:

1. ``register_job`` maps the Job protocol's ``idempotent`` flag onto
   apscheduler ``max_instances`` — ``False`` → 1 (never overlap), the
   long-documented but previously-untested overlap guard.
2. A fire skipped because the previous run is still in flight
   (EVENT_JOB_MAX_INSTANCES) is LOUD: warned in our logger, counted in
   ``get_stats``, and escalated as a deduped ``job_overlap_skipped``
   finding gated by ``scheduler_alert_on_job_overlap`` — not just
   apscheduler's own quiet internal warning, which is all the 2026-08-15
   80-minute tap overlap left behind.
"""

from __future__ import annotations

import logging
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from apscheduler.events import EVENT_JOB_MAX_INSTANCES

from plugins.scheduler import PluginScheduler


def _scheduler(*, overlap_alert: bool = True) -> PluginScheduler:
    sc = MagicMock()
    sc.get_bool.return_value = overlap_alert
    sc.get_int.return_value = 2
    return PluginScheduler(MagicMock(), site_config=sc)


def _job(name: str, *, idempotent: bool) -> SimpleNamespace:
    return SimpleNamespace(
        name=name,
        description="test job",
        schedule="every 1 hour",
        idempotent=idempotent,
    )


@pytest.mark.unit
def test_listener_registered_for_max_instances_event():
    sched = _scheduler()
    assert any(
        cb == sched._on_job_max_instances and (mask & EVENT_JOB_MAX_INSTANCES)
        for cb, mask in sched._scheduler._listeners
    )


@pytest.mark.unit
@pytest.mark.asyncio
async def test_register_job_maps_overlap_safety_to_max_instances():
    sched = _scheduler()
    seen: dict[str, int] = {}

    def _capture_add_job(func, trigger=None, **kwargs):
        seen[kwargs["id"]] = kwargs["max_instances"]

    cfg = SimpleNamespace(enabled=True, config={})
    cfg.get = lambda key, default=None: default

    with patch.object(sched, "_seed_job_config_if_absent", new=AsyncMock()), \
         patch.object(sched, "_interval_next_run", new=AsyncMock(return_value=None)), \
         patch("plugins.scheduler.PluginConfig") as plugin_config, \
         patch.object(sched._scheduler, "add_job", side_effect=_capture_add_job):
        plugin_config.load = AsyncMock(return_value=cfg)
        assert await sched.register_job(_job("serial_job", idempotent=False))
        assert await sched.register_job(_job("overlap_ok_job", idempotent=True))

    assert seen["serial_job"] == 1
    assert seen["overlap_ok_job"] == 3


@pytest.mark.unit
def test_overlap_skip_warns_counts_and_emits_finding(caplog):
    sched = _scheduler()
    with patch("utils.findings.emit_finding") as emit, \
         caplog.at_level(logging.WARNING, logger="plugins.scheduler"):
        sched._on_job_max_instances(SimpleNamespace(job_id="run_taps"))

    assert any("fire skipped" in rec.getMessage() for rec in caplog.records)
    assert sched.get_stats()["jobs_overlap_skipped"] == 1
    emit.assert_called_once()
    kwargs = emit.call_args.kwargs
    assert kwargs["kind"] == "job_overlap_skipped"
    assert kwargs["severity"] == "warn"  # info would never route (#1471)
    assert kwargs["dedup_key"] == "job-overlap:run_taps"
    assert kwargs["source"] == "scheduler.run_taps"


@pytest.mark.unit
def test_overlap_alert_switch_off_still_warns_but_no_finding(caplog):
    """``scheduler_alert_on_job_overlap=false`` is an operator opt-out of the
    findings escalation only — the log line and the counter stay."""
    sched = _scheduler(overlap_alert=False)
    with patch("utils.findings.emit_finding") as emit, \
         caplog.at_level(logging.WARNING, logger="plugins.scheduler"):
        sched._on_job_max_instances(SimpleNamespace(job_id="run_taps"))

    emit.assert_not_called()
    assert any("fire skipped" in rec.getMessage() for rec in caplog.records)
    assert sched.get_stats()["jobs_overlap_skipped"] == 1


@pytest.mark.unit
def test_overlap_listener_never_raises(caplog):
    """Listeners run inside apscheduler's dispatch — a findings failure must
    degrade to an error log, never propagate."""
    sched = _scheduler()
    with patch(
        "utils.findings.emit_finding", side_effect=RuntimeError("findings down")
    ), caplog.at_level(logging.ERROR, logger="plugins.scheduler"):
        sched._on_job_max_instances(SimpleNamespace(job_id="run_taps"))

    assert any(
        "failed to escalate overlap-skip" in rec.getMessage()
        for rec in caplog.records
    )
