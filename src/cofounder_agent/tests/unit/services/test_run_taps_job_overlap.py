"""RunTapsJob must never overlap itself (2026-08-15 incident).

The 02:06 hourly tap walk hung for 80 minutes on two wedged internal_rag
handlers, and the 03:06 fire started anyway — two full walks ran
concurrently, doubling embedding load on a GPU that was already OOM.

The ``Job`` protocol defines ``idempotent`` as "whether two overlapping
runs would be safe"; ``PluginScheduler.register_job`` maps ``False`` to
apscheduler ``max_instances=1``, so a fire whose predecessor is still in
flight is skipped (loudly, via the scheduler's EVENT_JOB_MAX_INSTANCES
listener — covered in tests/unit/plugins/test_scheduler_overlap_skip.py).
This pins RunTapsJob's side of that contract.
"""

from __future__ import annotations

import pytest

from services.jobs.run_taps import RunTapsJob

pytestmark = pytest.mark.unit


def test_run_taps_declares_overlap_unsafe():
    # False → max_instances=1: one walk at a time; a skipped fire waits for
    # the next hourly tick. Sequential re-runs stay safe (handlers dedupe) —
    # concurrency is the thing being forbidden here.
    assert RunTapsJob.idempotent is False


def test_run_taps_cadence_unchanged():
    # The overlap guard must not ride in with a cadence change — hourly is
    # still the floor set by the shortest per-tap schedule (hackernews).
    assert RunTapsJob.schedule == "every 1 hour"
