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
from services.media_infra_health import MediaInfraHealth
from services.site_config import SiteConfig


def _sc(**overrides):
    base = {
        "media_pipeline_trigger_enabled": "false",
        # Keep the render-infra health gate out of the legacy dispatch tests —
        # a real probe would hit the network. The gate has its own tests below
        # (which patch check_media_infra_health directly).
        "media_infra_healthcheck_enabled": "false",
    }
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


# ---------------------------------------------------------------------------
# Render-infra health gate (2026-07-03) — a dispatch fired into a
# wan/image-gen/DNS outage fast-fails and burns one of the task's bounded
# media_reconciliation re-dispatch attempts (six posts wedged at the cap this
# way). The job now probes before claiming and un-claims on an
# unhealthy-infra fast-fail.
# ---------------------------------------------------------------------------


def _sc_gated(**overrides):
    """Site config with the trigger on and the health gate ENABLED (tests
    patch check_media_infra_health, so no real probe runs)."""
    base = {
        "media_pipeline_trigger_enabled": "true",
        "media_infra_healthcheck_enabled": "true",
    }
    base.update(overrides)
    return SiteConfig(initial_config=base)


@pytest.mark.asyncio
async def test_unhealthy_infra_defers_cycle_without_claiming():
    """Unhealthy probe → the whole cycle defers: no claim, no run, markers and
    re-dispatch counts untouched, ok=True (an outage is not a job failure)."""
    job = DispatchMediaPipelineJob()
    pool = _FakePool([{"task_id": "t1"}])
    run_mock = AsyncMock()
    health = AsyncMock(return_value=MediaInfraHealth(False, "wan-server down"))
    with patch.object(dmp, "_run_media_pipeline", run_mock), patch.object(
        dmp, "check_media_infra_health", health
    ):
        out = await job.run(pool, {"_site_config": _sc_gated()})
    assert out.ok
    assert out.changes_made == 0
    assert "unhealthy" in out.detail
    pool.execute.assert_not_called()  # nothing claimed
    run_mock.assert_not_awaited()


@pytest.mark.asyncio
async def test_healthy_infra_dispatches_normally():
    job = DispatchMediaPipelineJob()
    pool = _FakePool([{"task_id": "t1"}], claim="UPDATE 1")
    run_mock = AsyncMock()
    health = AsyncMock(return_value=MediaInfraHealth(True, "ok"))
    with patch.object(dmp, "_run_media_pipeline", run_mock), patch.object(
        dmp, "check_media_infra_health", health
    ):
        out = await job.run(pool, {"_site_config": _sc_gated()})
    assert out.changes_made == 1
    run_mock.assert_awaited_once()


@pytest.mark.asyncio
async def test_no_eligible_rows_skips_the_probe():
    """Idle cycles must not pay probe round-trips — the health check runs
    only when there is work to gate."""
    job = DispatchMediaPipelineJob()
    pool = _FakePool([])
    health = AsyncMock(return_value=MediaInfraHealth(True, "ok"))
    with patch.object(dmp, "check_media_infra_health", health):
        out = await job.run(pool, {"_site_config": _sc_gated()})
    assert out.ok
    health.assert_not_awaited()


@pytest.mark.asyncio
async def test_infra_fast_fail_unclaims_and_stops_without_finding():
    """Run fails AND the post-failure probe is unhealthy → the piece is
    un-claimed (marker back to NULL, so no watchdog re-dispatch is burned to
    recover), no media_dispatch_failed finding, and the rest of the batch is
    deferred."""
    job = DispatchMediaPipelineJob()
    pool = _FakePool([{"task_id": "a"}, {"task_id": "b"}], claim="UPDATE 1")
    run_mock = AsyncMock(side_effect=RuntimeError("connect refused"))
    emit_mock = MagicMock()
    # Healthy at cycle start (dispatch proceeds), infra dead by the re-probe.
    health = AsyncMock(
        side_effect=[
            MediaInfraHealth(True, "ok"),
            MediaInfraHealth(False, "wan-server unreachable"),
        ]
    )
    with patch.object(dmp, "_run_media_pipeline", run_mock), patch.object(
        dmp, "check_media_infra_health", health
    ), patch.object(dmp, "emit_finding", emit_mock):
        out = await job.run(
            pool,
            {"_site_config": _sc_gated(media_pipeline_max_per_cycle="2")},
        )
    assert out.ok
    assert out.changes_made == 0
    emit_mock.assert_not_called()  # outage fast-fail is not a piece failure
    run_mock.assert_awaited_once()  # 'b' was never attempted (break)
    # Claim for 'a' + the un-claim revert.
    executed_sql = [c.args[0] for c in pool.execute.await_args_list]
    assert any("SET media_pipeline_dispatched_at = NOW()" in q for q in executed_sql)
    assert any("SET media_pipeline_dispatched_at = NULL" in q for q in executed_sql)


@pytest.mark.asyncio
async def test_attempt_vram_reclaim_calls_ollama_evict_and_hard_image_gen_unload():
    """_attempt_vram_reclaim must evict Ollama then hard-unload image-gen
    (PR 2, 2026-07-12) — the two reclaimable VRAM sources identified in the
    root-cause investigation."""
    from services.gpu_scheduler import gpu as real_gpu

    ollama_mock = AsyncMock()
    image_gen_mock = AsyncMock()
    with patch.object(real_gpu, "_unload_ollama_models", ollama_mock), \
         patch.object(real_gpu, "_unload_image_gen", image_gen_mock):
        await dmp._attempt_vram_reclaim(_sc_gated())
    ollama_mock.assert_awaited_once()
    image_gen_mock.assert_awaited_once_with(hard=True)


@pytest.mark.asyncio
async def test_vram_insufficient_triggers_reclaim_then_healthy_reprobe_dispatches():
    """VRAM-only unhealthy first probe -> reclaim attempted -> a healthy
    re-probe -> the cycle proceeds and dispatches this cycle (PR 2)."""
    job = DispatchMediaPipelineJob()
    pool = _FakePool([{"task_id": "t1"}], claim="UPDATE 1")
    run_mock = AsyncMock()
    reclaim_mock = AsyncMock()
    health = AsyncMock(
        side_effect=[
            MediaInfraHealth(False, "render-GPU free VRAM 20.0 GB < 25 GB", vram_insufficient=True),
            MediaInfraHealth(True, "ok"),
        ]
    )
    with patch.object(dmp, "_run_media_pipeline", run_mock), \
         patch.object(dmp, "check_media_infra_health", health), \
         patch.object(dmp, "_attempt_vram_reclaim", reclaim_mock), \
         patch.object(dmp.asyncio, "sleep", AsyncMock()):
        out = await job.run(pool, {"_site_config": _sc_gated()})
    reclaim_mock.assert_awaited_once()
    assert health.await_count == 2
    assert out.changes_made == 1
    run_mock.assert_awaited_once()


@pytest.mark.asyncio
async def test_vram_insufficient_reclaim_still_unhealthy_defers():
    """Reclaim attempted but the re-probe is still unhealthy -> defers as
    normal, exactly one reclaim attempt (bounded, not looped)."""
    job = DispatchMediaPipelineJob()
    pool = _FakePool([{"task_id": "t1"}])
    run_mock = AsyncMock()
    reclaim_mock = AsyncMock()
    health = AsyncMock(
        side_effect=[
            MediaInfraHealth(False, "render-GPU free VRAM 15.0 GB < 25 GB", vram_insufficient=True),
            MediaInfraHealth(False, "render-GPU free VRAM 16.0 GB < 25 GB", vram_insufficient=True),
        ]
    )
    with patch.object(dmp, "_run_media_pipeline", run_mock), \
         patch.object(dmp, "check_media_infra_health", health), \
         patch.object(dmp, "_attempt_vram_reclaim", reclaim_mock), \
         patch.object(dmp.asyncio, "sleep", AsyncMock()):
        out = await job.run(pool, {"_site_config": _sc_gated()})
    reclaim_mock.assert_awaited_once()
    assert health.await_count == 2
    assert out.changes_made == 0
    assert "unhealthy" in out.detail
    run_mock.assert_not_awaited()
    pool.execute.assert_not_called()


@pytest.mark.asyncio
async def test_non_vram_outage_never_attempts_reclaim():
    """A wan-server/image-gen/DNS outage (vram_insufficient=False) must NOT
    trigger a reclaim — restarting image-gen mid-outage is pointless. Single
    probe, defer, exactly as before PR 2."""
    job = DispatchMediaPipelineJob()
    pool = _FakePool([{"task_id": "t1"}])
    reclaim_mock = AsyncMock()
    health = AsyncMock(return_value=MediaInfraHealth(False, "wan-server down"))
    with patch.object(dmp, "check_media_infra_health", health), \
         patch.object(dmp, "_attempt_vram_reclaim", reclaim_mock):
        out = await job.run(pool, {"_site_config": _sc_gated()})
    reclaim_mock.assert_not_awaited()
    assert health.await_count == 1
    assert out.changes_made == 0


@pytest.mark.asyncio
async def test_reclaim_disabled_setting_skips_reclaim():
    """media_render_reclaim_enabled=false -> defer immediately on a
    VRAM-only unhealthy probe, no reclaim attempted even though
    vram_insufficient=True."""
    job = DispatchMediaPipelineJob()
    pool = _FakePool([{"task_id": "t1"}])
    reclaim_mock = AsyncMock()
    health = AsyncMock(
        return_value=MediaInfraHealth(False, "vram low", vram_insufficient=True)
    )
    with patch.object(dmp, "check_media_infra_health", health), \
         patch.object(dmp, "_attempt_vram_reclaim", reclaim_mock):
        out = await job.run(
            pool, {"_site_config": _sc_gated(media_render_reclaim_enabled="false")}
        )
    reclaim_mock.assert_not_awaited()
    assert health.await_count == 1
    assert out.changes_made == 0


@pytest.mark.asyncio
async def test_genuine_failure_on_healthy_infra_still_emits_finding():
    """Run fails but infra is healthy → the existing behaviour: marker stays
    set, a media_dispatch_failed finding fires, and the watchdog owns retry."""
    job = DispatchMediaPipelineJob()
    pool = _FakePool([{"task_id": "a"}], claim="UPDATE 1")
    run_mock = AsyncMock(side_effect=RuntimeError("render boom"))
    emit_mock = MagicMock()
    health = AsyncMock(return_value=MediaInfraHealth(True, "ok"))
    with patch.object(dmp, "_run_media_pipeline", run_mock), patch.object(
        dmp, "check_media_infra_health", health
    ), patch.object(dmp, "emit_finding", emit_mock):
        out = await job.run(pool, {"_site_config": _sc_gated()})
    assert out.ok
    assert emit_mock.call_count == 1
    # No un-claim: the only execute is the claim itself.
    executed_sql = [c.args[0] for c in pool.execute.await_args_list]
    assert not any("SET media_pipeline_dispatched_at = NULL" in q for q in executed_sql)
