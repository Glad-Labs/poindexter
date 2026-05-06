"""Unit tests for brain/gate_pending_summary_probe.py (Glad-Labs/poindexter#338).

Covers the coalesced "N posts pending review" summary probe: when the
pending queue is empty / young / unchanged we either skip Telegram
entirely or emit only the low-noise Discord status ping. When the
oldest gate has aged past the grace window, we send ONE coalesced
Telegram page per dedup window, and re-page mid-window only when the
queue grew by strictly more than the configured growth threshold.

All external I/O (the asyncpg pool, ``notify_operator``, the Discord
sender, ``datetime.now``) is mocked. The pool is a ``MagicMock`` whose
async methods are ``AsyncMock``s; we seed app_settings reads via the
``setting_values`` dict passed to ``_make_pool``.

Mirrors the shape of ``tests/unit/brain/test_backup_watcher.py`` and
the companion ``test_gate_auto_expire_probe.py`` so the brain probe
test suite stays consistent.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any, Optional
from unittest.mock import AsyncMock, MagicMock

import pytest

# pythonpath in pyproject.toml includes "../.." so the brain package
# resolves the same way the backup_watcher tests import it.
from brain import gate_pending_summary_probe as gps


# ---------------------------------------------------------------------------
# Helpers — fixed clock + pool builder
# ---------------------------------------------------------------------------


_FIXED_NOW = datetime(2026, 5, 6, 12, 0, 0, tzinfo=timezone.utc)


def _now_fn():
    return _FIXED_NOW


def _default_settings() -> dict[str, str]:
    """Match the migration's seed values."""
    return {
        gps.ENABLED_KEY: "true",
        gps.POLL_INTERVAL_MINUTES_KEY: "60",
        gps.MIN_AGE_MINUTES_KEY: "60",
        gps.TELEGRAM_DEDUP_MINUTES_KEY: "60",
        gps.TELEGRAM_GROWTH_THRESHOLD_KEY: "3",
        gps.DISCORD_PER_CYCLE_KEY: "true",
    }


def _make_pool(
    *,
    setting_values: Optional[dict[str, str]] = None,
    pending_count: int = 0,
    oldest_age_hours: Optional[float] = None,
    select_raises: Optional[Exception] = None,
):
    """Build an asyncpg-style mock pool that:

    - returns ``setting_values[key]`` for ``SELECT value FROM app_settings``
      lookups via ``fetchval``,
    - returns ``{"count": N, "oldest_created_at": ts}`` for the
      ``post_approval_gates`` aggregate via ``fetchrow``.
    """
    pool = MagicMock()
    settings = {**_default_settings(), **(setting_values or {})}

    if oldest_age_hours is None or pending_count == 0:
        oldest_ts: Optional[datetime] = None
    else:
        oldest_ts = _FIXED_NOW - timedelta(hours=oldest_age_hours)

    async def _fetchval(query, *args):
        if "app_settings" in query and args:
            return settings.get(args[0])
        return None

    async def _fetchrow(query, *args):
        if "post_approval_gates" in query:
            if select_raises is not None:
                raise select_raises
            return {
                "count": pending_count,
                "oldest_created_at": oldest_ts,
            }
        return None

    pool.fetchval = AsyncMock(side_effect=_fetchval)
    pool.fetchrow = AsyncMock(side_effect=_fetchrow)
    pool.fetch = AsyncMock(return_value=[])
    pool.execute = AsyncMock()
    return pool


@pytest.fixture(autouse=True)
def _clear_module_state():
    """Reset cross-cycle dedup state between scenarios.

    The probe stores ``last_real_pass_at`` / ``last_telegram_at`` /
    ``last_telegram_count`` at module level so the dedup window survives
    across brain cycles in production. Tests need a fresh slate.
    """
    gps._reset_state()
    yield
    gps._reset_state()


# ---------------------------------------------------------------------------
# Test 1 — no pending gates → no Telegram, Discord-only "queue empty"
# (since discord_per_cycle defaults to true)
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestEmptyQueue:
    @pytest.mark.asyncio
    async def test_no_pending_gates_no_telegram_discord_says_empty(self):
        pool = _make_pool(pending_count=0)

        notify_calls: list[dict] = []
        discord_calls: list[str] = []

        def fake_notify(**kwargs):
            notify_calls.append(kwargs)

        async def fake_discord(message: str) -> None:
            discord_calls.append(message)

        summary = await gps.run_gate_pending_summary_probe(
            pool, now_fn=_now_fn, notify_fn=fake_notify, discord_fn=fake_discord,
        )

        assert summary["ok"] is True
        assert summary["count"] == 0
        assert summary["telegram_sent"] is False
        # No Telegram page when the queue is empty.
        assert notify_calls == []
        # Discord per-cycle is on by default → one low-noise status.
        assert len(discord_calls) == 1
        assert "empty" in discord_calls[0].lower()
        assert summary["discord_sent"] is True


# ---------------------------------------------------------------------------
# Test 2 — pending gates younger than min_age → no Telegram, Discord status
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestWithinGraceWindow:
    @pytest.mark.asyncio
    async def test_young_gates_no_telegram_only_discord_status(self):
        # 5 pending, oldest is 30 min old; min_age default = 60 min.
        pool = _make_pool(pending_count=5, oldest_age_hours=0.5)

        notify_calls: list[dict] = []
        discord_calls: list[str] = []

        def fake_notify(**kwargs):
            notify_calls.append(kwargs)

        async def fake_discord(message: str) -> None:
            discord_calls.append(message)

        summary = await gps.run_gate_pending_summary_probe(
            pool, now_fn=_now_fn, notify_fn=fake_notify, discord_fn=fake_discord,
        )

        assert summary["count"] == 5
        assert summary["telegram_sent"] is False
        assert summary["telegram_skip_reason"] == "within_grace_window"
        assert notify_calls == []
        # Discord status still emitted with the count.
        assert len(discord_calls) == 1
        assert "5 posts pending review" in discord_calls[0]


# ---------------------------------------------------------------------------
# Test 3 — pending gates past min_age, FIRST run → Telegram fires + Discord
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestFirstPageOutsideGraceWindow:
    @pytest.mark.asyncio
    async def test_old_gates_first_run_telegram_and_discord_fire(self):
        # 5 pending, oldest is 3 hours old → past 60-min grace window.
        pool = _make_pool(pending_count=5, oldest_age_hours=3.0)

        notify_calls: list[dict] = []
        discord_calls: list[str] = []

        def fake_notify(**kwargs):
            notify_calls.append(kwargs)

        async def fake_discord(message: str) -> None:
            discord_calls.append(message)

        summary = await gps.run_gate_pending_summary_probe(
            pool, now_fn=_now_fn, notify_fn=fake_notify, discord_fn=fake_discord,
        )

        assert summary["count"] == 5
        assert summary["telegram_sent"] is True
        assert summary["status"] == "telegram_fired"

        # Exactly ONE coalesced Telegram page — never per-gate.
        assert len(notify_calls) == 1
        call = notify_calls[0]
        assert "5" in call["title"]
        assert "pending review" in call["title"].lower()
        # Body mentions count + age + the triage hint.
        detail = call["detail"]
        assert "5" in detail
        assert "hours old" in detail
        assert "poindexter gates list" in detail
        assert call["source"] == "brain.gate_pending_summary"
        assert call["severity"] == "warning"

        # Discord status also fired.
        assert len(discord_calls) == 1
        assert "5 posts" in discord_calls[0]


# ---------------------------------------------------------------------------
# Test 4 — same count two cycles in a row inside dedup window → Telegram
# fires ONCE
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestDedupSameCount:
    @pytest.mark.asyncio
    async def test_two_cycles_same_count_telegram_once(self):
        notify_calls: list[dict] = []

        def fake_notify(**kwargs):
            notify_calls.append(kwargs)

        async def fake_discord(message: str) -> None:
            pass

        # Lower poll interval so cycle 2's cadence gate doesn't suppress
        # us before the dedup logic gets a chance to.
        settings = {gps.POLL_INTERVAL_MINUTES_KEY: "1"}

        # Cycle 1: 5 pending, 3 hours old → fires.
        pool = _make_pool(
            setting_values=settings, pending_count=5, oldest_age_hours=3.0,
        )
        first_clock = _FIXED_NOW

        def first_now():
            return first_clock

        summary1 = await gps.run_gate_pending_summary_probe(
            pool, now_fn=first_now, notify_fn=fake_notify, discord_fn=fake_discord,
        )
        assert summary1["telegram_sent"] is True
        assert len(notify_calls) == 1

        # Cycle 2: same 5 pending, 30 min later. Cadence gate is open
        # (poll_interval=1 min), grace window passed (oldest > 60 min),
        # but we're INSIDE the 60-min Telegram dedup window AND the
        # count hasn't grown → no re-ping.
        second_clock = _FIXED_NOW + timedelta(minutes=30)
        pool2 = _make_pool(
            setting_values=settings, pending_count=5, oldest_age_hours=3.5,
        )

        def second_now():
            return second_clock

        summary2 = await gps.run_gate_pending_summary_probe(
            pool2, now_fn=second_now, notify_fn=fake_notify, discord_fn=fake_discord,
        )
        assert summary2["telegram_sent"] is False
        # Same count → no growth → deduped.
        assert "deduped" in summary2["telegram_skip_reason"].lower()
        assert len(notify_calls) == 1, (
            "Telegram must fire ONLY ONCE for an unchanged queue inside "
            "the dedup window"
        )


# ---------------------------------------------------------------------------
# Test 5 — count grows past growth_threshold inside dedup window → re-fires
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestDedupGrowthThreshold:
    @pytest.mark.asyncio
    async def test_growth_at_threshold_does_not_refire(self):
        """delta == threshold (3 == 3) is NOT 'strictly more than' →
        no re-ping. Acceptance criterion: 5→8 inside dedup window = quiet.
        """
        notify_calls: list[dict] = []

        def fake_notify(**kwargs):
            notify_calls.append(kwargs)

        async def fake_discord(message: str) -> None:
            pass

        # Lower poll interval so cadence gate doesn't suppress cycle 2
        # before the dedup logic runs.
        settings = {gps.POLL_INTERVAL_MINUTES_KEY: "1"}

        # Cycle 1: 5 pending → fires.
        pool1 = _make_pool(
            setting_values=settings, pending_count=5, oldest_age_hours=3.0,
        )
        await gps.run_gate_pending_summary_probe(
            pool1,
            now_fn=lambda: _FIXED_NOW,
            notify_fn=fake_notify,
            discord_fn=fake_discord,
        )
        assert len(notify_calls) == 1

        # Cycle 2, 30 min later (well inside 60-min dedup window):
        # delta = 8 - 5 = 3, threshold = 3 → NOT strictly more → no re-ping.
        second_clock = _FIXED_NOW + timedelta(minutes=30)
        pool2 = _make_pool(
            setting_values=settings, pending_count=8, oldest_age_hours=3.5,
        )
        summary2 = await gps.run_gate_pending_summary_probe(
            pool2,
            now_fn=lambda: second_clock,
            notify_fn=fake_notify,
            discord_fn=fake_discord,
        )
        assert summary2["telegram_sent"] is False, (
            "delta=3 is NOT strictly more than threshold=3 → no re-ping"
        )
        assert len(notify_calls) == 1

    @pytest.mark.asyncio
    async def test_growth_inside_dedup_window_specifically(self):
        """Tighter test: keep the second cycle strictly inside the dedup
        window so ONLY the growth-threshold branch can fire it."""
        notify_calls: list[dict] = []

        def fake_notify(**kwargs):
            notify_calls.append(kwargs)

        async def fake_discord(message: str) -> None:
            pass

        # Cycle 1 at _FIXED_NOW: 5 pending past grace → fires.
        pool1 = _make_pool(pending_count=5, oldest_age_hours=3.0)
        await gps.run_gate_pending_summary_probe(
            pool1,
            now_fn=lambda: _FIXED_NOW,
            notify_fn=fake_notify,
            discord_fn=fake_discord,
        )
        assert len(notify_calls) == 1

        # Cycle 2 — 30 min later (inside 60-min dedup AND inside 60-min
        # poll interval). To exercise the growth-threshold branch we
        # need the cadence gate ALSO out of the way; bump the poll
        # interval setting to 1 min so cadence doesn't suppress us.
        # But we want to keep the dedup window at 60 min so dedup
        # genuinely tries to suppress and gets overridden by growth.
        pool2 = _make_pool(
            setting_values={gps.POLL_INTERVAL_MINUTES_KEY: "1"},
            pending_count=9,  # delta = 4 > threshold 3
            oldest_age_hours=3.5,
        )
        second_clock = _FIXED_NOW + timedelta(minutes=30)
        summary2 = await gps.run_gate_pending_summary_probe(
            pool2,
            now_fn=lambda: second_clock,
            notify_fn=fake_notify,
            discord_fn=fake_discord,
        )
        assert summary2["telegram_sent"] is True, (
            "delta=4 > threshold=3 → must re-fire even inside dedup window"
        )
        assert len(notify_calls) == 2
        # Body mentions the re-page reason.
        assert "grew" in notify_calls[1]["detail"].lower()


# ---------------------------------------------------------------------------
# Test 6 — master switch off → no SQL fired
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestMasterSwitchOff:
    @pytest.mark.asyncio
    async def test_disabled_short_circuits_without_sql(self):
        pool = _make_pool(
            setting_values={gps.ENABLED_KEY: "false"},
            pending_count=999,
            oldest_age_hours=999.0,
        )

        notify_calls: list[dict] = []
        discord_calls: list[str] = []

        def fake_notify(**kwargs):
            notify_calls.append(kwargs)

        async def fake_discord(message: str) -> None:
            discord_calls.append(message)

        summary = await gps.run_gate_pending_summary_probe(
            pool, now_fn=_now_fn, notify_fn=fake_notify, discord_fn=fake_discord,
        )

        assert summary["status"] == "disabled"
        assert summary["telegram_sent"] is False
        assert summary["discord_sent"] is False
        # No SELECT against post_approval_gates was issued.
        gate_fetches = [
            c for c in pool.fetchrow.call_args_list
            if "post_approval_gates" in c.args[0]
        ]
        assert gate_fetches == []
        assert notify_calls == []
        assert discord_calls == []


# ---------------------------------------------------------------------------
# Discord per-cycle off → quiet behaviour
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestDiscordPerCycleOff:
    @pytest.mark.asyncio
    async def test_discord_off_silent_when_queue_empty(self):
        pool = _make_pool(
            setting_values={gps.DISCORD_PER_CYCLE_KEY: "false"},
            pending_count=0,
        )

        notify_calls: list[dict] = []
        discord_calls: list[str] = []

        def fake_notify(**kwargs):
            notify_calls.append(kwargs)

        async def fake_discord(message: str) -> None:
            discord_calls.append(message)

        summary = await gps.run_gate_pending_summary_probe(
            pool, now_fn=_now_fn, notify_fn=fake_notify, discord_fn=fake_discord,
        )
        assert summary["telegram_sent"] is False
        assert summary["discord_sent"] is False
        assert notify_calls == []
        assert discord_calls == []


# ---------------------------------------------------------------------------
# Probe wrapper coverage
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestProbeWrapper:
    @pytest.mark.asyncio
    async def test_probe_protocol_wrapper_returns_proberesult(self):
        pool = _make_pool(pending_count=0)

        async def fake_probe(_pool, **_kwargs):
            return {
                "ok": True,
                "status": "queue_empty",
                "count": 0,
                "telegram_sent": False,
                "discord_sent": True,
                "detail": "fake",
            }

        import brain.gate_pending_summary_probe as _mod
        original = _mod.run_gate_pending_summary_probe
        _mod.run_gate_pending_summary_probe = fake_probe  # type: ignore[assignment]
        try:
            probe = gps.GatePendingSummaryProbe()
            result = await probe.check(pool, {})
        finally:
            _mod.run_gate_pending_summary_probe = original  # type: ignore[assignment]

        assert result.ok is True
        assert result.detail == "fake"
        assert result.metrics["status"] == "queue_empty"
        assert result.metrics["count"] == 0
        assert result.metrics["telegram_sent"] is False
        assert result.metrics["discord_sent"] is True


# ---------------------------------------------------------------------------
# SELECT failure path — defensive coverage
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestSelectFailure:
    @pytest.mark.asyncio
    async def test_select_failure_surfaces_select_failed_status(self):
        pool = _make_pool(select_raises=RuntimeError("synthetic db kaboom"))

        notify_calls: list[dict] = []
        discord_calls: list[str] = []

        def fake_notify(**kwargs):
            notify_calls.append(kwargs)

        async def fake_discord(message: str) -> None:
            discord_calls.append(message)

        summary = await gps.run_gate_pending_summary_probe(
            pool, now_fn=_now_fn, notify_fn=fake_notify, discord_fn=fake_discord,
        )
        assert summary["ok"] is False
        assert summary["status"] == "select_failed"
        assert summary["count"] == 0
        assert summary["telegram_sent"] is False
        assert notify_calls == []
        # Discord per-cycle path also doesn't fire on select failure.
        assert discord_calls == []
