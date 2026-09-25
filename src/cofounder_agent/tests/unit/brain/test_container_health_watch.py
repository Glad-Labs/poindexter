"""Container health watch: alert while a container is stuck unhealthy.

2026-09-24: speaches wedged (a deadlocked model unload) and sat ``unhealthy``
for 154 minutes; every render lost its captions and nothing noticed, because
Docker restart policies act only on process exit. The probe is the
detector; restarting is a firefighter rule, so what matters here is WHEN it
fires, that it keeps firing (the firefighter's verify reads the repeats), and
that its rows keep the LLM long-tail from bouncing a container blind.
"""

from __future__ import annotations

import json
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from poindexter.brain import container_health_watch as chw

pytestmark = pytest.mark.asyncio


@pytest.fixture(autouse=True)
def _fresh():
    chw._reset_state()
    yield
    chw._reset_state()


def _pool(settings: dict[str, str] | None = None):
    settings = settings or {}
    pool = MagicMock()

    async def _fetchval(query, key):
        return settings.get(key)

    pool.fetchval = AsyncMock(side_effect=_fetchval)
    pool.execute = AsyncMock()
    return pool


def _c(name: str, status: str = "healthy", streak: int = 0, *,
       interval_s: float = 30.0, started: str = "2026-09-24T10:00:00Z"):
    return {
        "Name": f"/{name}",
        "State": {"Status": "running", "StartedAt": started,
                  "Health": {"Status": status, "FailingStreak": streak}},
        "Config": {"Healthcheck": {"Interval": int(interval_s * 1e9)}},
    }


def _rows(pool):
    out = []
    for call in pool.execute.call_args_list:
        args = call.args
        if "alert_events" in args[0]:
            out.append({
                "alertname": args[1], "status": args[2], "severity": args[3],
                "labels": json.loads(args[4]), "title": json.loads(args[5])["summary"],
                "body": json.loads(args[5])["description"], "fingerprint": args[6],
            })
    return out


async def _run(pool, containers):
    return await chw.run_container_health_watch_probe(
        pool, containers=containers, log_tail_fn=lambda n: f"{n} log tail",
    )


async def test_unhealthy_minutes_is_the_failing_streak_times_the_interval():
    assert chw.unhealthy_minutes(_c("x", "unhealthy", streak=20)) == pytest.approx(10.0)
    assert chw.unhealthy_minutes(_c("x", "unhealthy", streak=3, interval_s=60)) == pytest.approx(3.0)
    assert chw.unhealthy_minutes(_c("x", "healthy")) is None
    assert chw.unhealthy_minutes(_c("x", "starting", streak=3)) is None
    assert chw.unhealthy_minutes({"Name": "/x", "State": {}}) is None  # no healthcheck


async def test_a_wedged_container_fires_with_the_fields_a_firefighter_rule_matches():
    pool = _pool()
    summary = await _run(pool, [_c("poindexter-speaches", "unhealthy", streak=40)])  # 20 min
    (row,) = _rows(pool)
    assert row["alertname"] == "container_unhealthy"
    assert row["status"] == "firing" and row["severity"] == "warning"
    # Rules match `^container_health_watch:<name>\|` against this fingerprint.
    assert row["fingerprint"] == "container_health_watch:poindexter-speaches"
    assert row["labels"] == {"probe": "container_health_watch", "container": "poindexter-speaches",
                             "remediation": "rules_only"}
    assert "poindexter-speaches log tail" in row["body"]
    assert summary["firing"] == ["poindexter-speaches"]


async def test_the_rows_carry_the_label_value_the_engine_checks():
    from poindexter.brain.remediation.engine import RULES_ONLY

    assert chw.REMEDIATION_LABEL == RULES_ONLY


async def test_a_short_blip_is_left_alone():
    pool = _pool()
    summary = await _run(pool, [_c("poindexter-speaches", "unhealthy", streak=6)])  # 3 min
    assert _rows(pool) == []
    assert summary["unhealthy"] == ["poindexter-speaches"] and summary["firing"] == []


async def test_it_keeps_firing_every_cycle_while_the_container_stays_unhealthy():
    """The firefighter decides a restart failed when the alert fires again after it."""
    pool = _pool()
    for streak in (40, 50, 60):
        await _run(pool, [_c("poindexter-speaches", "unhealthy", streak=streak)])
    assert [r["status"] for r in _rows(pool)] == ["firing"] * 3


async def test_after_a_restart_starting_is_quiet_and_unhealthy_fires_at_once():
    """Once the episode is open, the threshold is spent: a container that comes
    back from a restart still broken must re-fire, or verify reads 'resolved'."""
    pool = _pool()
    await _run(pool, [_c("poindexter-speaches", "unhealthy", streak=40)])
    await _run(pool, [_c("poindexter-speaches", "starting", started="2026-09-24T11:00:00Z")])
    assert len(_rows(pool)) == 1
    await _run(pool, [_c("poindexter-speaches", "unhealthy", streak=5, started="2026-09-24T11:00:00Z")])
    assert [r["status"] for r in _rows(pool)] == ["firing", "firing"]


async def test_recovery_resolves_the_episode_and_says_whether_it_was_restarted():
    pool = _pool()
    await _run(pool, [_c("poindexter-speaches", "unhealthy", streak=40)])
    await _run(pool, [_c("poindexter-speaches", "healthy", started="2026-09-24T11:00:00Z")])
    last = _rows(pool)[-1]
    assert last["status"] == "resolved" and last["severity"] == "info"
    assert "after a restart" in last["title"]
    await _run(pool, [_c("poindexter-speaches", "healthy", started="2026-09-24T11:00:00Z")])
    assert len(_rows(pool)) == 2  # a calm container is not resolved twice

    pool = _pool()
    chw._reset_state()
    await _run(pool, [_c("poindexter-rife", "unhealthy", streak=40)])
    await _run(pool, [_c("poindexter-rife", "healthy")])
    assert "without a restart" in _rows(pool)[-1]["body"]


async def test_overrides_give_a_container_its_own_threshold():
    pool = _pool({chw.OVERRIDES_KEY: "poindexter-comfyui=30"})
    await _run(pool, [
        _c("poindexter-comfyui", "unhealthy", streak=44),   # 22 min, under its 30
        _c("poindexter-speaches", "unhealthy", streak=24),  # 12 min, past the default 10
    ])
    assert [r["fingerprint"] for r in _rows(pool)] == ["container_health_watch:poindexter-speaches"]
    await _run(pool, [_c("poindexter-comfyui", "unhealthy", streak=62)])  # 31 min
    assert _rows(pool)[-1]["fingerprint"] == "container_health_watch:poindexter-comfyui"


async def test_image_gen_is_judged_by_the_default_threshold():
    """image-gen shipped with a 30-minute default override: its /health was
    served on the event loop its inference blocked, so it read unhealthy for
    8-22 minutes during ordinary work. The GPU work moved to worker threads
    (glad-labs-stack#4021), so 12 minutes unhealthy is now a real wedge."""
    assert chw.DEFAULT_OVERRIDES == ""
    pool = _pool()
    await _run(pool, [_c("poindexter-image-gen-server", "unhealthy", streak=24)])  # 12 min
    assert [r["fingerprint"] for r in _rows(pool)] == [
        "container_health_watch:poindexter-image-gen-server",
    ]


async def test_code_defaults_match_the_seeded_defaults():
    """The probe falls back to its code default when a row is blank, and
    settings_defaults seeds the rows. Two copies of one default drift apart
    silently: the override outlived its reason in both places at once."""
    from poindexter.services.settings_defaults import DEFAULTS

    assert DEFAULTS[chw.OVERRIDES_KEY] == chw.DEFAULT_OVERRIDES
    assert DEFAULTS[chw.AFTER_MINUTES_KEY] == str(chw.DEFAULT_AFTER_MINUTES)


async def test_threshold_and_overrides_come_from_settings():
    pool = _pool({chw.AFTER_MINUTES_KEY: "2", chw.OVERRIDES_KEY: "poindexter-comfyui=60"})
    await _run(pool, [
        _c("poindexter-speaches", "unhealthy", streak=6),         # 3 min >= 2
        _c("poindexter-image-gen-server", "unhealthy", streak=6),  # override replaced: 3 >= 2
        _c("poindexter-comfyui", "unhealthy", streak=100),        # 50 min < 60
    ])
    assert sorted(r["labels"]["container"] for r in _rows(pool)) == [
        "poindexter-image-gen-server", "poindexter-speaches",
    ]


async def test_parse_overrides_skips_malformed_entries():
    assert chw.parse_overrides("a=30, b = 45 ,, bad, c=x, =5") == {"a": 30.0, "b": 45.0}


async def test_containers_without_a_healthcheck_are_ignored():
    pool = _pool()
    summary = await _run(pool, [{"Name": "/poindexter-promtail", "State": {"Status": "running"}}])
    assert _rows(pool) == [] and summary["unhealthy"] == []


async def test_disabled_does_nothing():
    pool = _pool({chw.ENABLED_KEY: "false"})
    summary = await _run(pool, [_c("poindexter-speaches", "unhealthy", streak=40)])
    assert summary["detail"] == "disabled" and _rows(pool) == []


async def test_docker_unreachable_reports_blind_without_raising():
    pool = _pool()
    with patch.object(chw, "inspect_stack_containers", return_value=None):
        summary = await chw.run_container_health_watch_probe(pool)
    assert summary["ok"] is False and "blind" in summary["detail"]


async def test_a_failed_alert_write_does_not_break_the_cycle():
    pool = _pool()
    pool.execute = AsyncMock(side_effect=RuntimeError("db down"))
    summary = await _run(pool, [_c("poindexter-speaches", "unhealthy", streak=40)])
    assert summary["ok"] is True and summary["firing"] == ["poindexter-speaches"]
