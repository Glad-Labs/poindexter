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
3. That escalation is severity-laddered for jobs whose interval is a POLL
   cadence rather than a runtime budget (``overlap_expected = True``).
   ``dispatch_media_pipeline`` polls every 5 min but renders for minutes,
   so it skipped 2-6 due fires per render BY DESIGN — 145 of the system's
   191 ``job_overlap_skipped`` findings over the 7d to 2026-08-27, the
   loudest kind in the system, every one of them correct behaviour. Such a
   job records ``info`` (board-only) until blocked past
   ``scheduler_overlap_alert_after_minutes``, then pages. Jobs that do NOT
   declare it are unchanged: they still page on the first skip.
"""

from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from apscheduler.events import EVENT_JOB_MAX_INSTANCES

from plugins.scheduler import PluginScheduler


def _scheduler(
    *, overlap_alert: bool = True, overlap_after_minutes: int = 60
) -> PluginScheduler:
    sc = MagicMock()
    sc.get_bool.return_value = overlap_alert
    sc.get_int.side_effect = lambda key, default=None: (
        overlap_after_minutes
        if key == "scheduler_overlap_alert_after_minutes"
        else 2
    )
    return PluginScheduler(MagicMock(), site_config=sc)


def _job(
    name: str, *, idempotent: bool, overlap_expected: bool = False
) -> SimpleNamespace:
    return SimpleNamespace(
        name=name,
        description="test job",
        schedule="every 1 hour",
        idempotent=idempotent,
        overlap_expected=overlap_expected,
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
    """A job that did NOT declare overlap_expected pages on the FIRST skip.

    Unchanged from the original contract (#1471) and load-bearing: for an
    hourly job like run_taps the first skip already means an hour elapsed, so
    deferring it would have made the 2026-08-15 tap wedge slower to surface."""
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


@pytest.mark.unit
@pytest.mark.asyncio
async def test_register_job_records_overlap_expected_declaration():
    """The listener only receives a job_id, so the declaration must be
    captured at registration time or the gate cannot see it."""
    sched = _scheduler()
    cfg = SimpleNamespace(enabled=True, config={})
    cfg.get = lambda key, default=None: default

    with patch.object(sched, "_seed_job_config_if_absent", new=AsyncMock()), \
         patch.object(sched, "_interval_next_run", new=AsyncMock(return_value=None)), \
         patch("plugins.scheduler.PluginConfig") as plugin_config, \
         patch.object(sched._scheduler, "add_job"):
        plugin_config.load = AsyncMock(return_value=cfg)
        await sched.register_job(
            _job("render_job", idempotent=False, overlap_expected=True)
        )
        await sched.register_job(_job("run_taps", idempotent=False))

    assert sched._overlap_expected_jobs == {"render_job"}


@pytest.mark.unit
def test_overlap_expected_job_records_info_not_warn_on_first_skip(caplog):
    """The 145-fires-of-noise case: a poll-cadence job skipping a due fire is
    the designed steady state. ``info`` is below the findings router's fetch
    floor, so it lands on the board and in get_stats without paging."""
    sched = _scheduler()
    sched._overlap_expected_jobs.add("dispatch_media_pipeline")

    with patch("utils.findings.emit_finding") as emit, \
         caplog.at_level(logging.WARNING, logger="plugins.scheduler"):
        sched._on_job_max_instances(
            SimpleNamespace(job_id="dispatch_media_pipeline")
        )

    kwargs = emit.call_args.kwargs
    assert kwargs["severity"] == "info"
    assert kwargs["kind"] == "job_overlap_skipped"
    # Still counted and still logged — recorded, not suppressed.
    assert sched.get_stats()["jobs_overlap_skipped"] == 1
    assert any("fire skipped" in rec.getMessage() for rec in caplog.records)


@pytest.mark.unit
def test_overlap_expected_job_escalates_to_warn_once_wedged():
    """A declaring job still pages — a render that never returns IS a wedge."""
    sched = _scheduler(overlap_after_minutes=60)
    sched._overlap_expected_jobs.add("dispatch_media_pipeline")
    # Blocked since 90 minutes ago: past the 60-minute threshold.
    sched._overlap_streak_started_at["dispatch_media_pipeline"] = datetime.now(
        timezone.utc
    ) - timedelta(minutes=90)

    with patch("utils.findings.emit_finding") as emit:
        sched._on_job_max_instances(
            SimpleNamespace(job_id="dispatch_media_pipeline")
        )

    kwargs = emit.call_args.kwargs
    assert kwargs["severity"] == "warn"
    assert kwargs["extra"]["blocked_minutes"] >= 90
    assert kwargs["extra"]["threshold_minutes"] == 60


@pytest.mark.unit
def test_overlap_streak_anchors_on_first_skip_not_latest():
    """Elapsed-since-first-skip is the whole signal; re-anchoring on every skip
    would reset the clock forever and the job could never escalate."""
    sched = _scheduler()
    sched._overlap_expected_jobs.add("render_job")
    anchor = datetime.now(timezone.utc) - timedelta(minutes=30)
    sched._overlap_streak_started_at["render_job"] = anchor

    with patch("utils.findings.emit_finding"):
        sched._on_job_max_instances(SimpleNamespace(job_id="render_job"))

    assert sched._overlap_streak_started_at["render_job"] == anchor


@pytest.mark.unit
def test_reset_overlap_streak_clears_the_anchor():
    """The run ended, so the job is not wedged — a stale anchor would let
    unrelated overlaps hours apart accumulate into a bogus page."""
    sched = _scheduler()
    sched._overlap_streak_started_at["render_job"] = datetime.now(timezone.utc)
    sched._reset_overlap_streak("render_job")
    assert "render_job" not in sched._overlap_streak_started_at
    # Idempotent for a job that never overlapped.
    sched._reset_overlap_streak("never_overlapped")
