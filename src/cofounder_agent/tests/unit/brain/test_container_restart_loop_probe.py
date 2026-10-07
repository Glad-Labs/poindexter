"""container_restart_loop_probe — pages once per loop episode, with the traceback, then recovers."""

from __future__ import annotations

import json
from unittest.mock import AsyncMock, MagicMock

import pytest

from poindexter.brain import container_restart_loop_probe as crl

T0 = 10_000.0


@pytest.fixture(autouse=True)
def _fresh():
    crl._reset_state()
    yield
    crl._reset_state()


def _pool(settings=None, baseline=None):
    settings = settings or {}
    pool = MagicMock()

    async def _fetchval(query, *args):
        return settings.get(args[0]) if args else None

    async def _fetch(query, *args):
        return [{"attribute": f"restarts:{k}", "value": str(v)} for k, v in (baseline or {}).items()]

    pool.fetchval = AsyncMock(side_effect=_fetchval)
    pool.fetch = AsyncMock(side_effect=_fetch)
    pool.execute = AsyncMock()
    return pool


def _c(name, count, status="running", restarting=False, health="healthy", exit_code=None):
    return {"name": name, "status": status, "restarting": restarting, "restart_count": count,
            "health": health, "started_at": "t",
            "exit_code": (1 if restarting else 0) if exit_code is None else exit_code, "image": f"img-{name}"}


def _alerts(pool):
    rows = []
    for call in pool.execute.call_args_list:
        sql = call.args[0]
        if "alert_events" in sql:
            rows.append({"status": call.args[2], "severity": call.args[3], "labels": json.loads(call.args[4]),
                         "annotations": json.loads(call.args[5]), "fingerprint": call.args[6]})
    return rows


def _persisted(pool):
    return {call.args[2].split(":", 1)[1]: int(call.args[3]) for call in pool.execute.call_args_list
            if "brain_knowledge" in call.args[0]}


async def _run(pool, containers, now, monkeypatch, tail="Traceback ...\nModuleNotFoundError: No module named '_voice_paths'"):
    monkeypatch.setattr(crl, "inspect_stack_containers", lambda: containers)
    monkeypatch.setattr(crl, "container_log_tail", lambda name, lines=15: tail)
    return await crl.run_container_restart_loop_probe(pool, now=now)


async def test_first_sight_records_a_baseline_and_never_pages(monkeypatch):
    pool = _pool()
    out = await _run(pool, [_c("poindexter-chatterbox", 507, status="restarting", restarting=True)], T0, monkeypatch)
    assert out["ok"] is True and out["looping"] == []
    assert _alerts(pool) == []
    assert _persisted(pool) == {"poindexter-chatterbox": 507}


async def test_a_jump_past_the_threshold_pages_critical_once_with_the_log_tail(monkeypatch):
    pool = _pool(baseline={"poindexter-chatterbox": 500})
    out = await _run(pool, [_c("poindexter-chatterbox", 504, status="restarting", restarting=True, health="unhealthy")], T0, monkeypatch)
    assert out["ok"] is False and out["looping"] == ["poindexter-chatterbox"] and out["paged"] == ["poindexter-chatterbox"]
    alerts = _alerts(pool)
    assert len(alerts) == 1
    a = alerts[0]
    assert a["status"] == "firing" and a["severity"] == "critical"
    assert a["fingerprint"] == "container_restart_loop_probe:poindexter-chatterbox:looping"
    assert "ModuleNotFoundError" in a["annotations"]["description"]
    assert "+4 this cycle" in a["annotations"]["summary"]
    # still looping five minutes later: no second page inside the reminder window
    pool.execute.reset_mock()
    out2 = await _run(pool, [_c("poindexter-chatterbox", 511, status="restarting", restarting=True)], T0 + 300, monkeypatch)
    assert out2["looping"] == ["poindexter-chatterbox"] and out2["paged"] == []
    assert _alerts(pool) == []


async def test_reminder_fires_after_the_configured_hour(monkeypatch):
    pool = _pool(baseline={"c": 10})
    await _run(pool, [_c("c", 20, restarting=True, status="restarting")], T0, monkeypatch)
    pool.execute.reset_mock()
    await _run(pool, [_c("c", 30, restarting=True, status="restarting")], T0 + 3601, monkeypatch)
    alerts = _alerts(pool)
    assert len(alerts) == 1 and alerts[0]["fingerprint"].endswith(f"reminder-{int((T0 + 3601) // 3600)}")


async def test_reminders_can_be_switched_off(monkeypatch):
    pool = _pool(settings={crl.REMINDER_HOURS_KEY: "0"}, baseline={"c": 10})
    await _run(pool, [_c("c", 20, restarting=True, status="restarting")], T0, monkeypatch)
    pool.execute.reset_mock()
    await _run(pool, [_c("c", 40, restarting=True, status="restarting")], T0 + 7200, monkeypatch)
    assert _alerts(pool) == []


async def test_restarting_with_a_high_count_but_slow_growth_is_still_a_loop(monkeypatch):
    pool = _pool(baseline={"c": 12})
    out = await _run(pool, [_c("c", 13, status="restarting", restarting=True)], T0, monkeypatch)
    assert out["looping"] == ["c"]


async def test_a_deploy_recreate_is_not_a_loop(monkeypatch):
    pool = _pool(baseline={"poindexter-worker": 0})
    out = await _run(pool, [_c("poindexter-worker", 1)], T0, monkeypatch)
    assert out["ok"] is True and out["looping"] == [] and _alerts(pool) == []


async def test_recovery_needs_two_calm_cycles_then_writes_a_resolved_row(monkeypatch):
    pool = _pool(baseline={"c": 0})
    await _run(pool, [_c("c", 5, restarting=True, status="restarting")], T0, monkeypatch)
    pool.execute.reset_mock()
    await _run(pool, [_c("c", 5)], T0 + 300, monkeypatch)       # calm 1
    assert _alerts(pool) == []
    out = await _run(pool, [_c("c", 5)], T0 + 600, monkeypatch)  # calm 2
    assert out["recovered"] == ["c"]
    alerts = _alerts(pool)
    assert len(alerts) == 1 and alerts[0]["status"] == "resolved" and alerts[0]["fingerprint"].endswith(":recovered")
    # a later loop is a NEW episode and pages again
    pool.execute.reset_mock()
    out2 = await _run(pool, [_c("c", 9, restarting=True, status="restarting")], T0 + 900, monkeypatch)
    assert out2["paged"] == ["c"]


async def test_docker_unreachable_is_reported_not_treated_as_quiet(monkeypatch):
    pool = _pool(baseline={"c": 3})
    monkeypatch.setattr(crl, "inspect_stack_containers", lambda: None)
    out = await crl.run_container_restart_loop_probe(pool, now=T0)
    assert out["ok"] is False and "unreachable" in out["detail"] and _alerts(pool) == []


async def test_kill_switch(monkeypatch):
    pool = _pool(settings={crl.ENABLED_KEY: "false"}, baseline={"c": 0})
    out = await _run(pool, [_c("c", 50, restarting=True, status="restarting")], T0, monkeypatch)
    assert out["detail"] == "disabled" and _alerts(pool) == []


def test_classify_table():
    assert crl.classify(_c("c", 3), None, 3) == "unknown"
    assert crl.classify(_c("c", 3), 0, 3) == "looping"
    assert crl.classify(_c("c", 2), 0, 3) == "calm"
    assert crl.classify(_c("c", 3, status="restarting", restarting=True), 3, 3) == "looping"
    assert crl.classify(_c("c", 1, status="exited"), 1, 3) == "calm"


# --- planned exits (2026-10-07) -------------------------------------------------
# GPU sidecars os._exit(0) to hand back a CUDA context and let the restart policy
# revive them. The 2026-10-06 page: a render unloaded image-gen three times in one
# cycle and the probe called a healthy container "restart-looping".

IMG = "poindexter-image-gen-server"


async def test_the_2026_10_06_page_is_now_a_planned_exit(monkeypatch):
    pool = _pool(baseline={IMG: 31})
    out = await _run(pool, [_c(IMG, 34)], T0, monkeypatch)
    assert out["ok"] is True and out["looping"] == [] and out["planned"] == [IMG]
    assert _alerts(pool) == []
    assert "planned exits: " + IMG in out["detail"]
    assert _persisted(pool) == {IMG: 34}, "the baseline still moves, so the next cycle measures from 34"


async def test_every_default_planned_exit_sidecar_is_excused(monkeypatch):
    names = crl.DEFAULT_PLANNED_EXIT_CONTAINERS.split(",")
    assert set(names) == {"poindexter-image-gen-server", "poindexter-wan-server", "poindexter-rife",
                          "poindexter-stable-audio", "poindexter-chatterbox"}
    pool = _pool(baseline=dict.fromkeys(names, 0))
    out = await _run(pool, [_c(n, 4) for n in names], T0, monkeypatch)
    assert out["looping"] == [] and sorted(out["planned"]) == sorted(names) and _alerts(pool) == []


async def test_a_container_not_on_the_list_still_pages_for_the_same_growth(monkeypatch):
    pool = _pool(baseline={"poindexter-worker": 31})
    out = await _run(pool, [_c("poindexter-worker", 34)], T0, monkeypatch)
    assert out["looping"] == ["poindexter-worker"] and len(_alerts(pool)) == 1


@pytest.mark.parametrize("bad", [
    {"exit_code": 1},                                      # it crashed
    {"exit_code": 137},                                    # OOM-killed
    {"exit_code": None, "restarting": True, "status": "restarting"},  # caught mid-backoff
    {"health": "unhealthy"},                               # back up, but wedged
    {"status": "exited"},                                  # did not come back
])
async def test_a_planned_exit_container_pages_when_the_exit_was_not_clean(monkeypatch, bad):
    pool = _pool(baseline={IMG: 31})
    out = await _run(pool, [_c(IMG, 34, **bad)], T0, monkeypatch)
    assert out["looping"] == [IMG] and out["planned"] == []
    assert len(_alerts(pool)) == 1 and _alerts(pool)[0]["severity"] == "critical"


async def test_clean_exits_past_the_per_cycle_ceiling_are_a_runaway(monkeypatch):
    pool = _pool(baseline={IMG: 0})
    out = await _run(pool, [_c(IMG, crl.DEFAULT_PLANNED_EXIT_MAX + 1)], T0, monkeypatch)
    assert out["looping"] == [IMG] and len(_alerts(pool)) == 1


async def test_the_ceiling_comes_from_settings(monkeypatch):
    pool = _pool(settings={crl.PLANNED_EXIT_MAX_KEY: "20"}, baseline={IMG: 0})
    out = await _run(pool, [_c(IMG, 15)], T0, monkeypatch)
    assert out["planned"] == [IMG] and _alerts(pool) == []


@pytest.mark.parametrize("settings", [
    {crl.PLANNED_EXIT_CONTAINERS_KEY: ""},     # an explicit empty list excuses no one
    {crl.PLANNED_EXIT_MAX_KEY: "0"},           # a zero ceiling excuses nothing
    {crl.PLANNED_EXIT_CONTAINERS_KEY: "poindexter-rife"},  # the operator's list replaces the default
])
async def test_the_exemption_can_be_narrowed_or_switched_off(monkeypatch, settings):
    pool = _pool(settings=settings, baseline={IMG: 31})
    out = await _run(pool, [_c(IMG, 34)], T0, monkeypatch)
    assert out["looping"] == [IMG] and len(_alerts(pool)) == 1


async def test_planned_exits_count_as_calm_when_an_episode_is_open(monkeypatch):
    # A real crash loop opens the episode; afterwards the sidecar goes back to its
    # ordinary idle exits, which must not hold the episode open forever.
    pool = _pool(baseline={IMG: 0})
    await _run(pool, [_c(IMG, 5, exit_code=1)], T0, monkeypatch)
    pool.execute.reset_mock()
    await _run(pool, [_c(IMG, 8)], T0 + 300, monkeypatch)           # planned, calm 1
    out = await _run(pool, [_c(IMG, 11)], T0 + 600, monkeypatch)    # planned, calm 2
    assert out["recovered"] == [IMG]
    assert [a["status"] for a in _alerts(pool)] == ["resolved"]


def test_classify_planned_exit_table():
    assert crl.classify(_c("c", 3), 0, 3, planned_exit_max=10) == "planned"
    assert crl.classify(_c("c", 3), 0, 3) == "looping"                               # not on the list
    assert crl.classify(_c("c", 3, exit_code=2), 0, 3, planned_exit_max=10) == "looping"
    assert crl.classify(_c("c", 11), 0, 3, planned_exit_max=10) == "looping"
    assert crl.classify(_c("c", 3), 0, 3, planned_exit_max=0) == "looping"
    assert crl.classify(_c("c", 2), 0, 3, planned_exit_max=10) == "calm"             # under threshold
    assert crl.classify(_c("c", 3, health=None), 0, 3, planned_exit_max=10) == "planned"   # no healthcheck
    assert crl.classify(_c("c", 3, health="starting"), 0, 3, planned_exit_max=10) == "planned"
