"""Unit tests for DispatchMediaPipelineJob — the Gate-1 → Stage-2 trigger.

The job is the scheduled dispatcher (#689 Plan 7): when a content piece clears
Gate 1 (``pipeline_tasks.status='approved'``) or is auto-published directly
(``status='published'`` — auto-publish can race the 5-min cron) and has
persisted Stage-1 media scripts, it kicks off a ``media_pipeline`` run —
but only when the operator has flipped ``media_pipeline_trigger_enabled`` on.
Default-OFF means the job is scheduled but dormant in prod.

Idempotency rides a claim-before-run marker (``media_pipeline_dispatched_at``):
the job stamps the column first, so a concurrent cycle or a worker restart
never re-dispatches the same piece.
"""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from services.jobs import dispatch_media_pipeline as dmp
from services.jobs.dispatch_media_pipeline import DispatchMediaPipelineJob
from services.site_config import SiteConfig


def _sc(**overrides):
    base = {"media_pipeline_trigger_enabled": "false"}
    base.update(overrides)
    return SiteConfig(initial_config=base)


class _FakePool:
    """Minimal asyncpg-pool stand-in — fetch returns rows, execute returns a
    command-tag string (``UPDATE 1`` / ``UPDATE 0``) like asyncpg."""

    def __init__(self, rows, claim="UPDATE 1"):
        self.fetch = AsyncMock(return_value=rows)
        self.execute = AsyncMock(return_value=claim)


@pytest.mark.asyncio
async def test_dormant_when_flag_off():
    """Flag off (default) → returns immediately, never touches the DB."""
    job = DispatchMediaPipelineJob()
    pool = _FakePool([{"task_id": "t1"}])
    out = await job.run(pool, {"_site_config": _sc()})
    assert out.ok
    assert out.changes_made == 0
    pool.fetch.assert_not_called()  # short-circuits before any query


@pytest.mark.asyncio
async def test_no_site_config_skips():
    job = DispatchMediaPipelineJob()
    out = await job.run(_FakePool([]), {})
    assert out.ok
    assert out.changes_made == 0


@pytest.mark.asyncio
async def test_no_pool_skips():
    job = DispatchMediaPipelineJob()
    out = await job.run(None, {"_site_config": _sc(media_pipeline_trigger_enabled="true")})
    assert out.ok
    assert out.changes_made == 0


@pytest.mark.asyncio
async def test_dispatches_eligible_task_under_media_thread():
    """Flag on + one eligible row → claims it and runs media_pipeline with the
    SOURCE task_id (so load_scripts finds the persisted scripts)."""
    job = DispatchMediaPipelineJob()
    pool = _FakePool([{"task_id": "abc"}], claim="UPDATE 1")
    run_mock = AsyncMock()
    with patch.object(dmp, "_run_media_pipeline", run_mock):
        out = await job.run(
            pool, {"_site_config": _sc(media_pipeline_trigger_enabled="true")}
        )
    assert out.changes_made == 1
    run_mock.assert_awaited_once()
    # Helper is called (pool, site_config, task_id) — task_id is the source id.
    args, _ = run_mock.call_args
    assert args[2] == "abc"
    # Claim happened before the run (marker stamped).
    pool.execute.assert_awaited_once()


@pytest.mark.asyncio
async def test_claim_race_skips_without_dispatch():
    """If the claim UPDATE affects 0 rows (another worker won the race), the
    piece is skipped — no media_pipeline run."""
    job = DispatchMediaPipelineJob()
    pool = _FakePool([{"task_id": "abc"}], claim="UPDATE 0")
    run_mock = AsyncMock()
    with patch.object(dmp, "_run_media_pipeline", run_mock):
        out = await job.run(
            pool, {"_site_config": _sc(media_pipeline_trigger_enabled="true")}
        )
    assert out.changes_made == 0
    run_mock.assert_not_awaited()


@pytest.mark.asyncio
async def test_dispatch_failure_emits_finding_and_continues():
    """A media_pipeline failure never halts the job (best-effort) — it emits a
    finding per failure and the job still returns ok."""
    job = DispatchMediaPipelineJob()
    pool = _FakePool([{"task_id": "a"}, {"task_id": "b"}], claim="UPDATE 1")
    run_mock = AsyncMock(side_effect=RuntimeError("render boom"))
    emit_mock = MagicMock()
    with patch.object(dmp, "_run_media_pipeline", run_mock), patch.object(
        dmp, "emit_finding", emit_mock
    ):
        out = await job.run(
            pool,
            {
                "_site_config": _sc(
                    media_pipeline_trigger_enabled="true",
                    media_pipeline_max_per_cycle="2",
                )
            },
        )
    assert out.ok  # best-effort
    assert out.changes_made == 0  # both runs failed
    assert emit_mock.call_count == 2  # one finding per failed piece


@pytest.mark.asyncio
async def test_dispatches_published_task():
    """A task that auto-published before the 5-min cron ran (status='published',
    media_pipeline_dispatched_at IS NULL) must still be dispatched.

    The _ELIGIBLE_SQL includes both 'approved' and 'published' to close the
    race where auto-publish moves the task past 'approved' before this job fires.
    The pool mock returns the row regardless of status — the SQL is what changed;
    this test documents the expected behaviour and guards against regressions
    that narrow the query back to 'approved'-only.
    """
    job = DispatchMediaPipelineJob()
    # Pool returns a row as if the SQL selected a published task.
    pool = _FakePool([{"task_id": "published-task-id"}], claim="UPDATE 1")
    run_mock = AsyncMock()
    with patch.object(dmp, "_run_media_pipeline", run_mock):
        out = await job.run(
            pool, {"_site_config": _sc(media_pipeline_trigger_enabled="true")}
        )
    assert out.changes_made == 1
    run_mock.assert_awaited_once()
    args, _ = run_mock.call_args
    assert args[2] == "published-task-id"


def test_eligible_sql_includes_published_status():
    """Guard: _ELIGIBLE_SQL must accept published tasks, not just approved."""
    assert "'published'" in dmp._ELIGIBLE_SQL
    assert "IN ('approved', 'published')" in dmp._ELIGIBLE_SQL


def test_eligible_sql_gates_on_podcast_script_not_shot_list():
    """Guard: gate must require podcast_script (minimum Stage-1 artifact), not
    video_shot_list.  Shot lists are optional — render nodes no-op when absent.
    Gating on the shot list would permanently block pre-shot-list tasks."""
    assert "podcast_script" in dmp._ELIGIBLE_SQL
    assert "video_shot_list" not in dmp._ELIGIBLE_SQL


def test_job_protocol_shape():
    """The job satisfies the Job protocol contract used by PluginScheduler."""
    job = DispatchMediaPipelineJob()
    assert job.name == "dispatch_media_pipeline"
    assert isinstance(job.schedule, str)
    # GPU-bound render — overlapping instances must NOT run concurrently.
    assert job.idempotent is False
