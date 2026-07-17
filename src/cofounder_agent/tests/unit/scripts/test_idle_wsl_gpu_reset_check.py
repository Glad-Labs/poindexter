"""Unit tests for scripts/idle_wsl_gpu_reset_check.py — the decision logic
behind the idle-only WSL/Docker GPU reset (PR 2, 2026-07-12 desktop-lockup
fix follow-up). All DB and Prometheus I/O is mocked; these tests pin the
reset CONDITIONS (idle, no active work, retention actually high, cooldown),
not connectivity. STATE_FILE is always patched to a pytest tmp_path so no
test ever touches the real ~/.poindexter/idle_wsl_reset_state.json.
"""
from __future__ import annotations

import importlib.util
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest


def _find_repo_root(start: Path) -> Path:
    for parent in start.resolve().parents:
        if (parent / "scripts" / "idle_wsl_gpu_reset_check.py").exists():
            return parent
    raise RuntimeError(
        "could not locate scripts/idle_wsl_gpu_reset_check.py from " + str(start)
    )


def _load_checker():
    script_path = (
        _find_repo_root(Path(__file__)) / "scripts" / "idle_wsl_gpu_reset_check.py"
    )
    spec = importlib.util.spec_from_file_location(
        "idle_wsl_gpu_reset_check_under_test", script_path,
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


checker = _load_checker()


# ---------------------------------------------------------------------------
# Small coercion helpers
# ---------------------------------------------------------------------------


def test_as_bool_recognizes_truthy_strings():
    assert checker._as_bool("true", False) is True
    assert checker._as_bool("TRUE", False) is True
    assert checker._as_bool("1", False) is True
    assert checker._as_bool("false", True) is False
    assert checker._as_bool(None, True) is True  # missing -> default
    assert checker._as_bool(None, False) is False


def test_as_float_falls_back_on_bad_value():
    assert checker._as_float("12.5", 0.0) == 12.5
    assert checker._as_float("", 9.0) == 9.0
    assert checker._as_float(None, 9.0) == 9.0
    assert checker._as_float("not-a-number", 9.0) == 9.0


def test_as_int_falls_back_on_bad_value():
    assert checker._as_int("5", 0) == 5
    assert checker._as_int(None, 3) == 3
    assert checker._as_int("nope", 3) == 3


# ---------------------------------------------------------------------------
# Cooldown state file roundtrip (STATE_FILE patched to tmp_path — never
# touches the real ~/.poindexter/ state on the host running these tests)
# ---------------------------------------------------------------------------


def test_cooldown_state_roundtrip(tmp_path):
    state_file = tmp_path / "idle_wsl_reset_state.json"
    with patch.object(checker, "STATE_FILE", state_file):
        assert checker._read_cooldown_state() is None
        checker._write_cooldown_state()
        stamped = checker._read_cooldown_state()
    assert stamped is not None
    assert (datetime.now(timezone.utc) - stamped).total_seconds() < 5


def test_cooldown_state_missing_file_reads_none(tmp_path):
    with patch.object(checker, "STATE_FILE", tmp_path / "does-not-exist.json"):
        assert checker._read_cooldown_state() is None


def test_cooldown_state_corrupt_file_reads_none(tmp_path):
    state_file = tmp_path / "state.json"
    state_file.write_text("not json", encoding="utf-8")
    with patch.object(checker, "STATE_FILE", state_file):
        assert checker._read_cooldown_state() is None


# ---------------------------------------------------------------------------
# decide() — the full condition set. All DB access goes through a fake
# asyncpg connection (fetch = settings rows, fetchval = the two COUNT(*)
# queries in call order); all Prometheus access is a bare async function
# patched directly (three calls in order: total_mib, used_mib, util_pct).
# ---------------------------------------------------------------------------


class _FakeConn:
    def __init__(self, settings_rows, in_progress=0, inflight=0):
        self.fetch = AsyncMock(return_value=settings_rows)
        self.fetchval = AsyncMock(side_effect=[in_progress, inflight])
        self.close = AsyncMock()


def _settings_rows(**overrides):
    base = {
        "idle_wsl_reset_enabled": "true",
        "idle_wsl_reset_min_idle_minutes": "20",
        "idle_wsl_reset_trigger_free_vram_gb": "28",
        "media_render_min_free_vram_gb": "25",
        "idle_wsl_reset_cooldown_hours": "6",
        "idle_wsl_reset_inflight_grace_minutes": "15",
        "gpu_busy_threshold_percent": "30",
        "pipeline_gpu_index": "0",
        "gpu_metrics_prometheus_url": "http://prometheus.test:9090",
    }
    base.update(overrides)
    return [{"key": k, "value": v} for k, v in base.items()]


def _patch_decide(conn, prom_values, tmp_path):
    """Context-manager-free patch bundle for one decide() call: DB connect,
    Prometheus reads (total_mib, used_mib, util_pct in that order), and a
    fresh empty state file (no prior reset -> cooldown always satisfied
    unless a test writes one itself)."""
    return (
        patch.object(checker.asyncpg, "connect", AsyncMock(return_value=conn)),
        patch.object(checker, "_query_prometheus_scalar", AsyncMock(side_effect=prom_values)),
        patch.object(checker, "STATE_FILE", tmp_path / "state.json"),
    )


@pytest.mark.asyncio
async def test_disabled_setting_blocks_reset(tmp_path):
    conn = _FakeConn(_settings_rows(idle_wsl_reset_enabled="false"))
    p1, p2, p3 = _patch_decide(conn, [30720.0, 5120.0, 2.0], tmp_path)
    with p1, p2, p3:
        result = await checker.decide(idle_minutes=60.0)
    assert result["should_reset"] is False
    assert any("enabled=false" in r for r in result["reasons"])


@pytest.mark.asyncio
async def test_user_active_blocks_reset(tmp_path):
    conn = _FakeConn(_settings_rows())
    p1, p2, p3 = _patch_decide(conn, [30720.0, 5120.0, 2.0], tmp_path)
    with p1, p2, p3:
        result = await checker.decide(idle_minutes=5.0)  # < default 20 min
    assert result["should_reset"] is False
    assert any("user active" in r for r in result["reasons"])


@pytest.mark.asyncio
async def test_in_progress_task_blocks_reset(tmp_path):
    conn = _FakeConn(_settings_rows(), in_progress=1)
    p1, p2, p3 = _patch_decide(conn, [30720.0, 5120.0, 2.0], tmp_path)
    with p1, p2, p3:
        result = await checker.decide(idle_minutes=60.0)
    assert result["should_reset"] is False
    assert any("in_progress" in r for r in result["reasons"])


@pytest.mark.asyncio
async def test_media_inflight_blocks_reset(tmp_path):
    conn = _FakeConn(_settings_rows(), inflight=1)
    p1, p2, p3 = _patch_decide(conn, [30720.0, 5120.0, 2.0], tmp_path)
    with p1, p2, p3:
        result = await checker.decide(idle_minutes=60.0)
    assert result["should_reset"] is False
    assert any("media dispatch" in r for r in result["reasons"])


@pytest.mark.asyncio
async def test_cooldown_active_blocks_reset(tmp_path):
    conn = _FakeConn(_settings_rows())
    state_file = tmp_path / "state.json"
    p1, p2, _ = _patch_decide(conn, [30720.0, 5120.0, 2.0], tmp_path)
    with p1, p2, patch.object(checker, "STATE_FILE", state_file):
        checker._write_cooldown_state()  # "just reset" -> well within 6h cooldown
        result = await checker.decide(idle_minutes=60.0)
    assert result["should_reset"] is False
    assert any("cooldown active" in r for r in result["reasons"])


@pytest.mark.asyncio
async def test_high_free_vram_blocks_reset(tmp_path):
    """total-used = 32768-2048 = 30 GiB free, above the 28 GB trigger
    -> retention isn't actually a problem, don't bounce the stack for nothing."""
    conn = _FakeConn(_settings_rows())
    p1, p2, p3 = _patch_decide(conn, [32768.0, 2048.0, 2.0], tmp_path)
    with p1, p2, p3:
        result = await checker.decide(idle_minutes=60.0)
    assert result["should_reset"] is False
    assert any("retention not high" in r for r in result["reasons"])
    assert result["checks"]["free_vram_gb"] == pytest.approx(30.0, abs=0.01)


# ---------------------------------------------------------------------------
# Trigger-vs-render-floor invariant (poindexter#881).
#
# media_render_min_free_vram_gb is the render floor: a media render defers
# unless the GPU has at least that many GB free. The reclaim exists to lift the
# GPU back over that floor, so a trigger BELOW the floor is incoherent — it
# leaves a dead band where renders defer (free < floor) yet the reclaim never
# fires (free >= trigger). The default trigger (28) sits above the floor (25),
# and decide() clamps any configured trigger up to the floor so the two can't
# silently drift into that gap.
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_dead_zone_between_old_trigger_and_floor_now_resets(tmp_path):
    """The exact band the bug lived in: 23.6 GB free — a render defers (< 25
    floor) but the OLD trigger of 22 left the reclaim ineligible (23.6 >= 22).
    With the trigger now above the floor, the reclaim fires."""
    conn = _FakeConn(_settings_rows())  # trigger 28, floor 25
    # total 32768 MiB, used 8602 -> 23.6 GiB free
    p1, p2, p3 = _patch_decide(conn, [32768.0, 8602.0, 2.0], tmp_path)
    with p1, p2, p3:
        result = await checker.decide(idle_minutes=60.0)
    assert result["should_reset"] is True
    assert result["reasons"] == ["all conditions satisfied"]
    assert result["checks"]["free_vram_gb"] == pytest.approx(23.6, abs=0.1)


@pytest.mark.asyncio
async def test_trigger_below_floor_is_clamped_up(tmp_path):
    """A trigger misconfigured below the floor is clamped up to it, so the
    reclaim can always at least fire when renders defer. trigger=20 < floor=25;
    at 23 GB free the raw trigger would block (23 >= 20) but the clamped-to-25
    trigger fires (23 < 25)."""
    conn = _FakeConn(_settings_rows(
        idle_wsl_reset_trigger_free_vram_gb="20",
        media_render_min_free_vram_gb="25",
    ))
    # total 32768, used 9216 -> 23.0 GiB free
    p1, p2, p3 = _patch_decide(conn, [32768.0, 9216.0, 2.0], tmp_path)
    with p1, p2, p3:
        result = await checker.decide(idle_minutes=60.0)
    assert result["should_reset"] is True
    assert result["checks"]["trigger_free_vram_gb"] == pytest.approx(25.0, abs=0.01)
    assert result["checks"]["configured_trigger_free_vram_gb"] == pytest.approx(20.0, abs=0.01)
    assert result["checks"]["trigger_clamped_to_floor"] is True


@pytest.mark.asyncio
async def test_trigger_below_floor_clamp_still_blocks_when_ample(tmp_path):
    """The clamp doesn't over-fire: trigger=20 clamped up to floor=25, but at
    26 GB free that's still above the effective trigger, so no reset."""
    conn = _FakeConn(_settings_rows(
        idle_wsl_reset_trigger_free_vram_gb="20",
        media_render_min_free_vram_gb="25",
    ))
    # total 32768, used 6144 -> 26.0 GiB free
    p1, p2, p3 = _patch_decide(conn, [32768.0, 6144.0, 2.0], tmp_path)
    with p1, p2, p3:
        result = await checker.decide(idle_minutes=60.0)
    assert result["should_reset"] is False
    assert any("retention not high" in r for r in result["reasons"])


@pytest.mark.asyncio
async def test_gpu_busy_blocks_reset():
    """Low free VRAM but the GPU is actively busy (>= threshold) -> treat as
    active use (external workload or an unowned pipeline run), don't reset."""
    conn = _FakeConn(_settings_rows())
    import tempfile
    with tempfile.TemporaryDirectory() as td:
        tmp_path = Path(td)
        p1, p2, p3 = _patch_decide(conn, [30720.0, 25600.0, 45.0], tmp_path)  # 5 GiB free, 45% util
        with p1, p2, p3:
            result = await checker.decide(idle_minutes=60.0)
    assert result["should_reset"] is False
    assert any("GPU busy" in r for r in result["reasons"])


@pytest.mark.asyncio
async def test_unreadable_prometheus_fails_closed(tmp_path):
    conn = _FakeConn(_settings_rows())
    p1, p2, p3 = _patch_decide(conn, [None, None, None], tmp_path)
    with p1, p2, p3:
        result = await checker.decide(idle_minutes=60.0)
    assert result["should_reset"] is False
    assert any("unreadable" in r for r in result["reasons"])


@pytest.mark.asyncio
async def test_all_conditions_satisfied_allows_reset(tmp_path):
    """The happy path: idle long enough, nothing in progress, VRAM genuinely
    low (5 GiB free < 28 GB trigger), GPU idle (2%), no cooldown."""
    conn = _FakeConn(_settings_rows())
    p1, p2, p3 = _patch_decide(conn, [30720.0, 25600.0, 2.0], tmp_path)
    with p1, p2, p3:
        result = await checker.decide(idle_minutes=60.0)
    assert result["should_reset"] is True
    assert result["reasons"] == ["all conditions satisfied"]


@pytest.mark.asyncio
async def test_decide_closes_connection_even_on_error():
    """A DB error mid-decide must not leak the connection."""
    conn = _FakeConn(_settings_rows())
    conn.fetch = AsyncMock(side_effect=RuntimeError("connection reset"))
    with patch.object(checker.asyncpg, "connect", AsyncMock(return_value=conn)):
        with pytest.raises(RuntimeError):
            await checker.decide(idle_minutes=60.0)
    conn.close.assert_awaited_once()
