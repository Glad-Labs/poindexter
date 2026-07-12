"""Unit tests for ``IngestCorsairCsvJob`` — fast-cadence local iCUE feed ingest.

RunTapsJob walks *every* enabled tap on one hourly cadence — correct for the
network taps, but it leaves the local corsair_csv feed (iCUE rewrites the CSV
every 30s) up to ~60 min stale even while healthy. This job ingests
corsair_csv on its own 5-min cadence via ``run_all(only_names=[...])`` so the
data_freshness_probe threshold can drop from 120m to 30m without false pages.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest

from plugins.job import JobResult
from services.integrations.tap_runner import RunSummary, TapResult
from services.jobs.ingest_corsair_csv import IngestCorsairCsvJob


def test_job_metadata_declares_fast_cadence():
    """The whole point of this job: a 5-min cadence vs RunTapsJob's hourly."""
    job = IngestCorsairCsvJob()
    assert job.name == "ingest_corsair_csv"
    assert job.schedule == "every 5 minutes"
    assert job.idempotent is True


@pytest.mark.asyncio
async def test_run_scopes_runner_to_corsair_only():
    """Must invoke the runner restricted to corsair_csv — never walk the
    hourly network taps (that would hammer remote APIs every 5 min)."""
    job = IngestCorsairCsvJob()
    summary = RunSummary(
        taps=[TapResult(name="corsair_csv", ok=True, duration_ms=12, records=75)],
        total_records=75,
        total_failed=0,
    )
    with patch(
        "services.integrations.tap_runner.run_all",
        new=AsyncMock(return_value=summary),
    ) as run_all:
        res = await job.run(pool=object(), config={"_site_config": "SC"})

    run_all.assert_awaited_once()
    assert run_all.await_args.kwargs["only_names"] == ["corsair_csv"]
    assert run_all.await_args.kwargs["site_config"] == "SC"
    assert isinstance(res, JobResult)
    assert res.ok is True
    assert res.changes_made == 75


@pytest.mark.asyncio
async def test_run_no_tap_configured_is_clean_noop():
    """OSS operators without iCUE have no corsair_csv row — the runner
    returns an empty summary and the job is a silent success, not a page."""
    job = IngestCorsairCsvJob()
    summary = RunSummary(taps=[], total_records=0, total_failed=0)
    with patch(
        "services.integrations.tap_runner.run_all",
        new=AsyncMock(return_value=summary),
    ):
        res = await job.run(pool=object(), config={})
    assert res.ok is True
    assert res.changes_made == 0


@pytest.mark.asyncio
async def test_run_surfaces_tap_failure():
    """A failing ingest must return ok=False so the scheduler records it —
    silent tap failure is exactly how the feed goes dark unnoticed."""
    job = IngestCorsairCsvJob()
    summary = RunSummary(
        taps=[
            TapResult(
                name="corsair_csv",
                ok=False,
                duration_ms=5,
                error="ValueError: directory required",
            )
        ],
        total_records=0,
        total_failed=1,
    )
    with patch(
        "services.integrations.tap_runner.run_all",
        new=AsyncMock(return_value=summary),
    ):
        res = await job.run(pool=object(), config={})
    assert res.ok is False
    assert "corsair_csv" in res.detail


@pytest.mark.asyncio
async def test_run_swallows_runner_exception():
    """The runner raising must not crash the scheduler cycle — a probe/job
    that dies stops watching everything after it."""
    job = IngestCorsairCsvJob()
    with patch(
        "services.integrations.tap_runner.run_all",
        new=AsyncMock(side_effect=RuntimeError("boom")),
    ):
        res = await job.run(pool=object(), config={})
    assert res.ok is False
    assert "boom" in res.detail
