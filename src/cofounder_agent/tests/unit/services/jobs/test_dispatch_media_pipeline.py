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
    # Claim happened before the run (marker stamped), then the un-claim budget
    # was refunded after it (poindexter#995) — two writes, in that order.
    executed = [c.args[0] for c in pool.execute.await_args_list]
    assert len(executed) == 2
    assert "SET media_pipeline_dispatched_at = NOW()" in executed[0]
    assert "SET media_pipeline_unclaim_count = 0" in executed[1]


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
         patch.object(real_gpu, "_unload_image_gen", image_gen_mock), \
         patch.object(real_gpu, "_unload_chatterbox", AsyncMock()), \
         patch.object(real_gpu, "_unload_wan", AsyncMock()):
        await dmp._attempt_vram_reclaim(_sc_gated())
    ollama_mock.assert_awaited_once()
    image_gen_mock.assert_awaited_once_with(hard=True)


@pytest.mark.asyncio
async def test_attempt_vram_reclaim_also_unloads_chatterbox():
    """Glad-Labs/poindexter#940: the chatterbox TTS sidecar caches its model
    after narrating and is NOT a GPU-lock owner, so before this it sat outside
    every reclaim lever and squatted through the following video render
    (observed deferring on "free VRAM 24.0 GB < 25 GB required").

    Soft, not hard: what it holds is the model, not a wedged CUDA context, so
    there's no reason to bounce the process and pay a cold reload."""
    from services.gpu_scheduler import gpu as real_gpu

    chatterbox_mock = AsyncMock()
    with patch.object(real_gpu, "_unload_ollama_models", AsyncMock()), \
         patch.object(real_gpu, "_unload_image_gen", AsyncMock()), \
         patch.object(real_gpu, "_unload_chatterbox", chatterbox_mock), \
         patch.object(real_gpu, "_unload_wan", AsyncMock()):
        await dmp._attempt_vram_reclaim(_sc_gated())
    chatterbox_mock.assert_awaited_once_with()


@pytest.mark.asyncio
async def test_attempt_vram_reclaim_hard_unloads_wan():
    """Glad-Labs/poindexter#962: wan's idle unloader drops the pipeline
    objects but the process keeps ~10 GB of CUDA reserved pool, which no
    lever could touch — the gate then deferred every render overnight
    ("free VRAM 18.6 GB < 25 GB required") until a human restarted the
    container. Hard, because only a process exit returns the reserved pool;
    the server declines (nothing_to_reclaim) below its floor, so repeat
    reclaims are cheap."""
    from services.gpu_scheduler import gpu as real_gpu

    wan_mock = AsyncMock()
    with patch.object(real_gpu, "_unload_ollama_models", AsyncMock()), \
         patch.object(real_gpu, "_unload_image_gen", AsyncMock()), \
         patch.object(real_gpu, "_unload_chatterbox", AsyncMock()), \
         patch.object(real_gpu, "_unload_wan", wan_mock):
        await dmp._attempt_vram_reclaim(_sc_gated())
    wan_mock.assert_awaited_once_with(hard=True)


@pytest.mark.asyncio
async def test_attempt_vram_reclaim_survives_a_failing_lever():
    """Every callee is best-effort by contract, so one raising must neither
    abort the reclaim nor bubble into the cycle.

    This is load-bearing for the lever ORDER: chatterbox runs last, so before
    the levers were isolated a stray error in the Ollama evict would silently
    skip it — costing exactly the reclaim this path was extended to gain."""
    from services.gpu_scheduler import gpu as real_gpu

    image_gen_mock = AsyncMock()
    chatterbox_mock = AsyncMock()
    wan_mock = AsyncMock()
    with patch.object(
            real_gpu, "_unload_ollama_models",
            AsyncMock(side_effect=RuntimeError("ollama unreachable"))), \
         patch.object(real_gpu, "_unload_image_gen", image_gen_mock), \
         patch.object(real_gpu, "_unload_chatterbox", chatterbox_mock), \
         patch.object(real_gpu, "_unload_wan", wan_mock):
        await dmp._attempt_vram_reclaim(_sc_gated())  # must not raise

    # The levers AFTER the failure still ran — including wan, the last rung.
    image_gen_mock.assert_awaited_once_with(hard=True)
    chatterbox_mock.assert_awaited_once_with()
    wan_mock.assert_awaited_once_with(hard=True)


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


# ---------------------------------------------------------------------------
# Reclaim cooldown (2026-07-28)
# ---------------------------------------------------------------------------
# The per-cycle guards were already right — reclaim only with eligible work AND
# a specifically-VRAM failure. What was missing was memory ACROSS cycles: on a
# 5-minute cron a reclaim that cannot help simply repeats forever. Observed
# 2026-07-27: fired every cycle for 2+ hours, freed nothing each time, and each
# one restarted image-gen, opening a cold-start window in which /generate
# failed and article images silently downgraded to stock. The video render
# never happened either way, so every one of those exits was pure loss.


@pytest.fixture(autouse=True)
def _reset_reclaim_cooldown():
    """Cooldown state is a module global — isolate it per test.

    Without this the first test to record an ineffective reclaim would
    suppress reclaims in every test that ran after it, and the resulting
    failure would look like an unrelated ordering flake.
    """
    dmp._last_ineffective_reclaim = None
    yield
    dmp._last_ineffective_reclaim = None


@pytest.mark.asyncio
async def test_ineffective_reclaim_starts_cooldown_and_emits_finding():
    """A reclaim that runs and leaves the gate unhealthy must arm the cooldown
    and say so — silence is what let this repeat unnoticed for hours."""
    job = DispatchMediaPipelineJob()
    pool = _FakePool([{"task_id": "t1"}], claim="UPDATE 1")
    emit_mock = MagicMock()
    health = AsyncMock(
        return_value=MediaInfraHealth(
            False, "render-GPU free VRAM 15.0 GB < 25 GB", vram_insufficient=True,
        )
    )
    with patch.object(dmp, "_run_media_pipeline", AsyncMock()), \
         patch.object(dmp, "check_media_infra_health", health), \
         patch.object(dmp, "_attempt_vram_reclaim", AsyncMock()), \
         patch.object(dmp, "emit_finding", emit_mock), \
         patch.object(dmp.asyncio, "sleep", AsyncMock()):
        out = await job.run(pool, {"_site_config": _sc_gated()})

    assert out.changes_made == 0
    assert dmp._last_ineffective_reclaim is not None
    kinds = [c.kwargs.get("kind") for c in emit_mock.call_args_list]
    assert "vram_reclaim_ineffective" in kinds


@pytest.mark.asyncio
async def test_second_cycle_skips_reclaim_while_cooling_down():
    """The actual regression: back-to-back cycles must not each restart
    image-gen. Cycle 2 still defers, it just does not pay for another
    pointless reclaim to get there."""
    job = DispatchMediaPipelineJob()
    reclaim_mock = AsyncMock()
    unhealthy = MediaInfraHealth(
        False, "render-GPU free VRAM 15.0 GB < 25 GB", vram_insufficient=True,
    )
    with patch.object(dmp, "_run_media_pipeline", AsyncMock()), \
         patch.object(dmp, "check_media_infra_health", AsyncMock(return_value=unhealthy)), \
         patch.object(dmp, "_attempt_vram_reclaim", reclaim_mock), \
         patch.object(dmp, "emit_finding", MagicMock()), \
         patch.object(dmp.asyncio, "sleep", AsyncMock()):
        await job.run(_FakePool([{"task_id": "t1"}], claim="UPDATE 1"),
                      {"_site_config": _sc_gated()})
        assert reclaim_mock.await_count == 1

        out2 = await job.run(_FakePool([{"task_id": "t2"}], claim="UPDATE 1"),
                             {"_site_config": _sc_gated()})

    assert reclaim_mock.await_count == 1, "cycle 2 must not re-run the reclaim"
    assert out2.changes_made == 0


@pytest.mark.asyncio
async def test_successful_reclaim_clears_cooldown():
    """A reclaim that works must leave no cooldown behind, so the next genuine
    VRAM squeeze is handled immediately instead of being suppressed."""
    job = DispatchMediaPipelineJob()
    dmp._last_ineffective_reclaim = None
    health = AsyncMock(
        side_effect=[
            MediaInfraHealth(False, "render-GPU free VRAM 20.0 GB < 25 GB", vram_insufficient=True),
            MediaInfraHealth(True, "ok"),
        ]
    )
    with patch.object(dmp, "_run_media_pipeline", AsyncMock()), \
         patch.object(dmp, "check_media_infra_health", health), \
         patch.object(dmp, "_attempt_vram_reclaim", AsyncMock()), \
         patch.object(dmp.asyncio, "sleep", AsyncMock()):
        out = await job.run(_FakePool([{"task_id": "t1"}], claim="UPDATE 1"),
                            {"_site_config": _sc_gated()})

    assert out.changes_made == 1
    assert dmp._last_ineffective_reclaim is None


@pytest.mark.asyncio
async def test_cooldown_expires_and_allows_a_fresh_reclaim():
    """Cooldown is a pause, not a permanent stop."""
    import time as _time

    dmp._last_ineffective_reclaim = _time.monotonic() - (31 * 60)
    sc = _sc_gated(media_render_reclaim_cooldown_minutes="30")
    assert dmp._reclaim_on_cooldown(sc, now=_time.monotonic()) == 0.0


def test_cooldown_of_zero_disables_the_feature():
    """An operator setting 0 opts back into the old every-cycle behaviour."""
    import time as _time

    dmp._last_ineffective_reclaim = _time.monotonic()
    sc = _sc_gated(media_render_reclaim_cooldown_minutes="0")
    assert dmp._reclaim_on_cooldown(sc, now=_time.monotonic()) == 0.0


# ---------------------------------------------------------------------------
# Bounded outage un-claim budget (poindexter#995)
#
# The un-claim path is free of the RE-DISPATCH budget on purpose — an outage
# must never wedge a piece at that cap. But "free" was also "unbounded": the
# post-failure probe cannot tell "infra died independently" from "this render's
# own VRAM footprint is why the probe fails" (wan holds 23-27 GB mid-render),
# so a piece that fails because of ITSELF was re-claimed forever. Task 8faf3617
# rode that loop to 40 findings in 24h with redispatch_count still reading 0.
# ---------------------------------------------------------------------------


class _RoutingPool:
    """Pool whose ``execute`` returns a command tag chosen by SQL content, so a
    test can make the claim succeed while the un-claim is refused."""

    def __init__(self, rows, *, unclaim_tag="UPDATE 1", default_tag="UPDATE 1"):
        self.fetch = AsyncMock(return_value=rows)
        self._unclaim_tag = unclaim_tag
        self._default_tag = default_tag
        self.calls: list[tuple] = []
        self.execute = AsyncMock(side_effect=self._execute)

    async def _execute(self, sql, *args):
        self.calls.append((sql, args))
        if "media_pipeline_unclaim_count = media_pipeline_unclaim_count + 1" in sql:
            return self._unclaim_tag
        return self._default_tag

    def sql_matching(self, needle):
        return [(s, a) for s, a in self.calls if needle in s]


def _fail_then_unhealthy():
    """Healthy at cycle start (so the dispatch proceeds), dead by the re-probe."""
    return AsyncMock(
        side_effect=[
            MediaInfraHealth(True, "ok"),
            MediaInfraHealth(False, "render GPU free VRAM 18.6 GB < 25 GB required"),
        ]
    )


@pytest.mark.asyncio
async def test_unclaim_is_bounded_by_the_configured_budget():
    """The un-claim UPDATE carries the budget as a bind param and bumps the
    counter — the WHERE clause is what makes the loop terminate."""
    job = DispatchMediaPipelineJob()
    pool = _RoutingPool([{"task_id": "a"}])
    with patch.object(dmp, "_run_media_pipeline", AsyncMock(side_effect=RuntimeError("boom"))), \
         patch.object(dmp, "check_media_infra_health", _fail_then_unhealthy()), \
         patch.object(dmp, "emit_finding", MagicMock()):
        out = await job.run(
            pool, {"_site_config": _sc_gated(media_pipeline_unclaim_max="5")}
        )
    assert out.ok
    unclaims = pool.sql_matching("media_pipeline_unclaim_count = media_pipeline_unclaim_count + 1")
    assert len(unclaims) == 1
    sql, args = unclaims[0]
    assert "SET media_pipeline_dispatched_at = NULL" in sql
    assert "AND media_pipeline_unclaim_count < $2" in sql
    assert args == ("a", 5)  # budget is bound, not interpolated


@pytest.mark.asyncio
async def test_spent_budget_leaves_marker_set_and_emits_finding():
    """0-row un-claim = budget spent. The marker stays set (piece stops
    re-rendering) and a finding fires — 'stopped looping' must not look
    identical to 'silently wedged'."""
    job = DispatchMediaPipelineJob()
    pool = _RoutingPool([{"task_id": "loop-victim"}], unclaim_tag="UPDATE 0")
    emit_mock = MagicMock()
    with patch.object(dmp, "_run_media_pipeline", AsyncMock(side_effect=RuntimeError("boom"))), \
         patch.object(dmp, "check_media_infra_health", _fail_then_unhealthy()), \
         patch.object(dmp, "emit_finding", emit_mock):
        out = await job.run(pool, {"_site_config": _sc_gated()})
    assert out.ok
    emit_mock.assert_called_once()
    kwargs = emit_mock.call_args.kwargs
    assert kwargs["kind"] == "media_unclaim_budget_exhausted"
    assert kwargs["extra"]["task_id"] == "loop-victim"
    assert kwargs["extra"]["unclaim_max"] == 3
    # dedup per task: two different wedged pieces must not silence each other.
    assert kwargs["dedup_key"] == "media_unclaim_budget_exhausted:loop-victim"


@pytest.mark.asyncio
async def test_healthy_infra_failure_still_emits_dispatch_failed_not_budget_finding():
    """A failure on HEALTHY infra is a bad piece, not an outage — it keeps the
    pre-existing media_dispatch_failed path and never touches the budget."""
    job = DispatchMediaPipelineJob()
    pool = _RoutingPool([{"task_id": "a"}])
    emit_mock = MagicMock()
    health = AsyncMock(side_effect=[MediaInfraHealth(True, "ok"), MediaInfraHealth(True, "ok")])
    with patch.object(dmp, "_run_media_pipeline", AsyncMock(side_effect=RuntimeError("boom"))), \
         patch.object(dmp, "check_media_infra_health", health), \
         patch.object(dmp, "emit_finding", emit_mock):
        await job.run(pool, {"_site_config": _sc_gated()})
    assert emit_mock.call_args.kwargs["kind"] == "media_dispatch_failed"
    assert not pool.sql_matching("media_pipeline_unclaim_count = media_pipeline_unclaim_count + 1")


@pytest.mark.asyncio
async def test_successful_dispatch_refunds_the_budget():
    """A clean render resets the counter, so the ceiling caps CONSECUTIVE
    self-inflicted retries rather than imposing a lifetime quota."""
    job = DispatchMediaPipelineJob()
    pool = _RoutingPool([{"task_id": "good"}])
    with patch.object(dmp, "_run_media_pipeline", AsyncMock()), \
         patch.object(dmp, "check_media_infra_health", AsyncMock(return_value=MediaInfraHealth(True, "ok"))):
        out = await job.run(pool, {"_site_config": _sc_gated()})
    assert out.changes_made == 1
    resets = pool.sql_matching("SET media_pipeline_unclaim_count = 0")
    assert len(resets) == 1
    assert resets[0][1] == ("good",)


@pytest.mark.asyncio
async def test_budget_reset_failure_does_not_fail_a_successful_render():
    """Bookkeeping is best-effort — a reset error must not turn a completed
    ~15-minute GPU render into a job failure."""
    job = DispatchMediaPipelineJob()
    pool = _RoutingPool([{"task_id": "good"}])

    async def _boom(sql, *args):
        pool.calls.append((sql, args))
        if "SET media_pipeline_unclaim_count = 0" in sql:
            raise RuntimeError("deadlock")
        return "UPDATE 1"

    pool.execute = AsyncMock(side_effect=_boom)
    with patch.object(dmp, "_run_media_pipeline", AsyncMock()), \
         patch.object(dmp, "check_media_infra_health", AsyncMock(return_value=MediaInfraHealth(True, "ok"))):
        out = await job.run(pool, {"_site_config": _sc_gated()})
    assert out.ok
    assert out.changes_made == 1


def test_unclaim_max_parsing():
    """Default 3; garbage falls back to 3; 0 is honoured (an operator choosing
    'never un-claim, always let the bounded watchdog own it') and must NOT be
    silently promoted to 1; negatives floor at 0."""
    assert dmp._unclaim_max(_sc_gated()) == 3
    assert dmp._unclaim_max(_sc_gated(media_pipeline_unclaim_max="7")) == 7
    assert dmp._unclaim_max(_sc_gated(media_pipeline_unclaim_max="not-a-number")) == 3
    assert dmp._unclaim_max(_sc_gated(media_pipeline_unclaim_max="")) == 3
    assert dmp._unclaim_max(_sc_gated(media_pipeline_unclaim_max="0")) == 0
    assert dmp._unclaim_max(_sc_gated(media_pipeline_unclaim_max="-4")) == 0


@pytest.mark.asyncio
async def test_budget_zero_never_unclaims():
    """Budget 0 → the guarded UPDATE can never match, so the marker always
    stays set and the watchdog owns every retry."""
    job = DispatchMediaPipelineJob()
    pool = _RoutingPool([{"task_id": "a"}], unclaim_tag="UPDATE 0")
    emit_mock = MagicMock()
    with patch.object(dmp, "_run_media_pipeline", AsyncMock(side_effect=RuntimeError("boom"))), \
         patch.object(dmp, "check_media_infra_health", _fail_then_unhealthy()), \
         patch.object(dmp, "emit_finding", emit_mock):
        await job.run(pool, {"_site_config": _sc_gated(media_pipeline_unclaim_max="0")})
    assert pool.sql_matching("media_pipeline_unclaim_count = media_pipeline_unclaim_count + 1")[0][1] == ("a", 0)
    assert emit_mock.call_args.kwargs["kind"] == "media_unclaim_budget_exhausted"
