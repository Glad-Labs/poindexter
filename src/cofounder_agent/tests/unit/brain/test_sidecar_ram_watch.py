"""Sidecar host-RAM recycle watch (brain/sidecar_ram_watch.py).

The generalisation of comfyui_ram_watch forced by the 2026-08-27 freeze:
comfyui_ram_watch is hardcoded to poindexter-comfyui, which held ~2 GB that
day, while chatterbox/speaches/wan-server/stable-audio held ~29 GB with no
watcher at all.

The dangerous failure here is a FALSE IDLE — restarting a sidecar mid-render.
Most of these tests are about that.
"""
from __future__ import annotations

import asyncio
import json
from unittest.mock import AsyncMock, MagicMock

import pytest
from brain import sidecar_ram_watch as sw


class _Pool:
    """Minimal asyncpg-pool stand-in: app_settings reads + audit_log writes."""

    def __init__(self, settings: dict | None = None, gpu_lock_held: bool = False):
        self._settings = settings or {}
        self._gpu_lock_held = gpu_lock_held
        self.execute = AsyncMock()

    async def fetchval(self, sql: str, *args):
        if "pg_locks" in sql:
            return self._gpu_lock_held
        key = args[0] if args else None
        return self._settings.get(key)


def _run(coro):
    return asyncio.run(coro)


def _summary(pool, **kw):
    kw.setdefault("gpu_lock_fn", AsyncMock(return_value=False))
    kw.setdefault("cpu_fn", lambda c: 0.5)
    kw.setdefault("mem_fn", lambda c: (1.0, 9.0))
    kw.setdefault("restart_fn", lambda c: (True, "ok"))
    kw.setdefault("now_fn", lambda: 10_000.0)
    return _run(sw.run_sidecar_ram_watch_probe(pool, **kw))


@pytest.fixture(autouse=True)
def _clean_state():
    sw._reset_recycle_state()
    yield
    sw._reset_recycle_state()


# --- config ------------------------------------------------------------------


def test_disabled_is_a_noop():
    s = _summary(_Pool({sw.ENABLED_KEY: "false"}))
    assert s["status"] == "disabled"


def test_empty_target_list_watches_nothing():
    s = _summary(_Pool({sw.TARGETS_KEY: ""}))
    assert s["status"] == "no_targets"


@pytest.mark.parametrize(
    "raw,expected",
    [
        ("a:6", [("a", 6.0)]),
        ("a:6,b:4", [("a", 6.0), ("b", 4.0)]),
        ("  a : 6 , b:4 ", [("a", 6.0), ("b", 4.0)]),
        ("a:6,,b:4", [("a", 6.0), ("b", 4.0)]),
        ("", []),
        (None, []),
    ],
)
def test_parse_targets(raw, expected):
    assert sw.parse_targets(raw) == expected


#: Measured RSS+swap per container at 14:20 EDT on 2026-08-27, minutes before
#: the hard reboot (Prometheus container_memory_rss + container_memory_swap).
INCIDENT_FOOTPRINTS_GB = {
    "poindexter-chatterbox": 10.1,
    "poindexter-speaches": 8.0,
    "poindexter-wan-server": 7.6,
    "poindexter-stable-audio": 3.9,
}


def test_incident_footprints_would_have_tripped():
    """Every shipped watermark must sit BELOW that sidecar's real 08-27
    footprint, or the probe watches the incident it exists for and does
    nothing.

    This caught a live off-by-a-hair: stable-audio shipped at 4 GB against a
    3.9 GB incident footprint, so the one sidecar whose number was chosen
    FROM the incident was the one it would have missed.
    """
    defaults = dict(sw.parse_targets(sw.DEFAULT_TARGETS))
    assert set(defaults) == set(INCIDENT_FOOTPRINTS_GB), (
        "default targets and the incident record have drifted apart"
    )
    for container, footprint in INCIDENT_FOOTPRINTS_GB.items():
        assert defaults[container] < footprint, (
            f"{container} watermark {defaults[container]} GB >= its "
            f"{footprint} GB incident footprint — would NOT have fired"
        )


def test_shipped_defaults_match_app_settings_seed():
    """DEFAULT_TARGETS and the app_settings seed are a drift pair."""
    from cofounder_agent.services.settings_defaults import DEFAULTS

    assert sw.parse_targets(DEFAULTS["sidecar_ram_recycle_targets"]) == (
        sw.parse_targets(sw.DEFAULT_TARGETS)
    )


@pytest.mark.parametrize("bad", ["noWatermark", "a:notanumber", "a:0", "a:-3", ":6"])
def test_parse_targets_skips_malformed_without_dropping_the_rest(bad):
    """One fat-fingered entry must not disarm the others, and must NOT be
    silently defaulted to some invented watermark."""
    parsed = sw.parse_targets(f"good:6,{bad},other:4")
    assert ("good", 6.0) in parsed
    assert ("other", 4.0) in parsed
    assert all(name not in ("", "a", "noWatermark") for name, _ in parsed)


# --- the idle gates (the part that must not be wrong) ------------------------


def test_gpu_lock_held_blocks_the_recycle():
    restart = MagicMock()
    s = _summary(
        _Pool(),
        gpu_lock_fn=AsyncMock(return_value=True),
        restart_fn=restart,
    )
    assert s["status"] == "busy"
    assert "scheduler lock" in s["detail"]
    restart.assert_not_called()


def test_unknown_gpu_lock_state_counts_as_busy():
    """#3094 posture: unprovable == busy. A failed lock query must never be
    read as 'nothing running'."""
    restart = MagicMock()
    s = _summary(
        _Pool(),
        gpu_lock_fn=AsyncMock(return_value=None),
        restart_fn=restart,
    )
    assert s["status"] == "busy"
    assert "unknown" in s["detail"]
    restart.assert_not_called()


def test_gpu_gate_can_be_disabled_but_cpu_gate_still_applies():
    """Turning gate 1 off must not turn the probe into a blind restarter —
    the per-container CPU gate is the one that stays."""
    restart = MagicMock()
    pool = _Pool(
        {sw.TARGETS_KEY: "solo:6", sw.REQUIRE_GPU_LOCK_FREE_KEY: "false"}
    )
    # GPU lock HELD, but the gate is off and the container is idle -> recycles.
    s = _summary(pool, gpu_lock_fn=AsyncMock(return_value=True))
    assert s["status"] == "recycled"

    sw._reset_recycle_state()
    # Same config, but the container is busy -> still declines.
    s = _summary(
        pool,
        gpu_lock_fn=AsyncMock(return_value=True),
        cpu_fn=lambda c: 90.0,
        restart_fn=restart,
    )
    assert s["status"] == "busy"
    restart.assert_not_called()


def test_gpu_lock_is_not_even_queried_when_the_gate_is_off():
    """Skipping the gate must skip its cost, not just its verdict."""
    lock = AsyncMock(return_value=True)
    _summary(
        _Pool({sw.TARGETS_KEY: "solo:6", sw.REQUIRE_GPU_LOCK_FREE_KEY: "false"}),
        gpu_lock_fn=lock,
    )
    lock.assert_not_awaited()


def test_busy_cpu_blocks_the_recycle():
    restart = MagicMock()
    s = _summary(_Pool(), cpu_fn=lambda c: 80.0, restart_fn=restart)
    assert s["status"] == "busy"
    restart.assert_not_called()


def test_unreadable_cpu_counts_as_busy():
    restart = MagicMock()
    s = _summary(_Pool(), cpu_fn=lambda c: None, restart_fn=restart)
    assert s["status"] == "busy"
    restart.assert_not_called()


def test_cpu_exactly_at_threshold_is_busy():
    """Boundary is >=, so the threshold value itself does not count as idle."""
    restart = MagicMock()
    s = _summary(
        _Pool({sw.CPU_IDLE_PERCENT_KEY: "5"}),
        cpu_fn=lambda c: 5.0,
        restart_fn=restart,
    )
    assert s["status"] == "busy"
    restart.assert_not_called()


def test_idle_proof_is_taken_after_the_footprint_read_not_before():
    """The GPU lock must be consulted AFTER the (slow) memory reads, so a
    session that started in between still blocks the restart."""
    calls: list[str] = []

    def _mem(c):
        calls.append("mem")
        return (1.0, 9.0)

    async def _lock():
        calls.append("lock")
        return False

    _summary(_Pool(), mem_fn=_mem, gpu_lock_fn=_lock)
    assert calls[-1] == "lock", f"lock check must come last, got {calls}"


# --- watermark + selection ---------------------------------------------------


def test_below_watermark_never_restarts():
    restart = MagicMock()
    s = _summary(_Pool(), mem_fn=lambda c: (0.2, 0.3), restart_fn=restart)
    assert s["status"] == "below_watermark"
    restart.assert_not_called()


def test_recycles_only_the_fattest_target_per_cycle():
    """Blast-radius bound: several fat sidecars must not all bounce at once."""
    sizes = {
        "poindexter-chatterbox": (1.0, 9.0),   # 10.0
        "poindexter-speaches": (0.5, 7.5),     # 8.0
        "poindexter-wan-server": (0.0, 7.6),   # 7.6
        "poindexter-stable-audio": (0.0, 5.0),  # 5.0
    }
    restarted: list[str] = []
    s = _summary(
        _Pool(),
        mem_fn=lambda c: sizes[c],
        restart_fn=lambda c: (restarted.append(c), (True, "ok"))[1],
    )
    assert s["status"] == "recycled"
    assert restarted == ["poindexter-chatterbox"]


def test_unreadable_footprint_is_skipped_not_treated_as_zero():
    """A profile-gated sidecar that isn't up is normal, not a fault — but it
    also must not be counted as 0 GB and quietly 'pass'."""
    s = _summary(_Pool(), mem_fn=lambda c: None)
    assert s["status"] == "below_watermark"
    assert any("unreadable" in x for x in s["skipped"])


# --- cooldown ----------------------------------------------------------------

def test_cooldown_blocks_a_second_recycle_of_the_same_container():
    pool = _Pool({sw.TARGETS_KEY: "solo:6", sw.COOLDOWN_MINUTES_KEY: "60"})
    now = {"t": 10_000.0}
    first = _summary(pool, now_fn=lambda: now["t"])
    assert first["status"] == "recycled"

    now["t"] += 30 * 60  # 30 min later, inside the 60 min cooldown
    restart = MagicMock()
    second = _summary(pool, now_fn=lambda: now["t"], restart_fn=restart)
    assert second["status"] == "below_watermark"
    assert any("cooldown" in x for x in second["skipped"])
    restart.assert_not_called()


def test_cooldown_is_per_container_not_global():
    """A recycled chatterbox must not shield a separately-fat speaches."""
    pool = _Pool({sw.TARGETS_KEY: "a:6,b:6", sw.COOLDOWN_MINUTES_KEY: "60"})
    now = {"t": 10_000.0}
    restarted: list[str] = []

    def _restart(c):
        restarted.append(c)
        return (True, "ok")

    # 'a' and 'b' both fat; the fattest ('a') goes first.
    _summary(
        pool,
        mem_fn=lambda c: (1.0, 9.0) if c == "a" else (1.0, 8.0),
        restart_fn=_restart,
        now_fn=lambda: now["t"],
    )
    now["t"] += 60
    _summary(
        pool,
        mem_fn=lambda c: (1.0, 9.0) if c == "a" else (1.0, 8.0),
        restart_fn=_restart,
        now_fn=lambda: now["t"],
    )
    assert restarted == ["a", "b"], (
        "second cycle must fall through to 'b' while 'a' cools down"
    )


# --- findings ----------------------------------------------------------------


def test_successful_recycle_emits_info_finding():
    pool = _Pool({sw.TARGETS_KEY: "solo:6"})
    s = _summary(pool)
    assert s["status"] == "recycled"
    pool.execute.assert_awaited()
    args = pool.execute.await_args.args
    details = json.loads(args[2])
    assert details["kind"] == "sidecar_ram_recycled"
    assert args[3] == "info"  # info never routes — board-visible only


def test_restart_failure_emits_warn_finding_and_reports_not_ok():
    pool = _Pool({sw.TARGETS_KEY: "solo:6"})
    s = _summary(pool, restart_fn=lambda c: (False, "docker exploded"))
    assert s["ok"] is False
    assert s["status"] == "restart_failed"
    details = json.loads(pool.execute.await_args.args[2])
    assert details["kind"] == "sidecar_ram_recycle_failed"
    assert pool.execute.await_args.args[3] == "warn"


def test_failed_recycle_does_not_start_the_cooldown():
    """A failed restart must be retried next cycle, not sat out for an hour."""
    pool = _Pool({sw.TARGETS_KEY: "solo:6"})
    _summary(pool, restart_fn=lambda c: (False, "nope"))
    assert sw._last_recycle_monotonic == {}


# --- GPU lock query ----------------------------------------------------------


def test_gpu_lock_key_matches_the_scheduler_constant():
    """The brain cannot import the worker package, so the key is duplicated.
    Pin it against the real constant so the two cannot drift apart."""
    from cofounder_agent.services.gpu_scheduler import GPU_ADVISORY_LOCK_KEY

    assert sw.GPU_ADVISORY_LOCK_KEY == GPU_ADVISORY_LOCK_KEY


def test_gpu_lock_held_reads_the_pool():
    assert _run(sw.gpu_lock_held(_Pool(gpu_lock_held=True))) is True
    assert _run(sw.gpu_lock_held(_Pool(gpu_lock_held=False))) is False


def test_gpu_lock_query_failure_is_unknown_not_free():
    class _Boom:
        async def fetchval(self, *a, **k):
            raise RuntimeError("pg gone")

    assert _run(sw.gpu_lock_held(_Boom())) is None
