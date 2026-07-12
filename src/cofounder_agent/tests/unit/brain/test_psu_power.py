"""Unit tests for ``brain/psu_power.py``.

Pins the electricity-cost wall-power priority chain (Shelly outlet meter →
iCUE CSV tap → software estimate → static floor) and the graduated,
flap-suppressed PSU-watchdog alert transitions.

History: added 2026-06-03 when the iCUE tap was wired in as a real-power
fallback. Reworked 2026-07-12 after an incident where the host exporter's
per-request `/metrics` collection intermittently exceeded the brain's 3s
scrape timeout — dropping the Shelly reading *and* the software estimate at
once, falling to the 150W floor, and paging Telegram ~15×/day on 1-cycle
blips. The primary source is now the Shelly outlet meter (HWiNFO was retired
2026-07-10), and the watchdog debounces: a degraded source must persist for
`degraded_cycles_before_page` consecutive cycles before it pages, so a single
slow scrape self-heals silently.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest
from brain.psu_power import (
    STATIC_DEFAULT_WATTS,
    fetch_icue_psu_watts,
    psu_watchdog_transition,
    select_power_source,
)

# --- select_power_source -------------------------------------------------

def test_shelly_meter_is_primary():
    """The metered wall-power reading (psu_total_power_watts, now the Shelly
    outlet meter) is the primary source, labelled ``shelly``."""
    watts, source = select_power_source(290.0, 305.0, 90.0)
    assert (watts, source) == (290.0, "shelly")


def test_icue_fallback_when_meter_absent():
    """Meter dropped (None) → use the iCUE tap value, not the estimate."""
    watts, source = select_power_source(None, 305.0, 90.0)
    assert (watts, source) == (305.0, "icue")


def test_estimate_when_both_psu_sources_absent():
    watts, source = select_power_source(None, None, 90.0)
    assert (watts, source) == (90.0, "estimate")


def test_static_floor_when_nothing_available():
    watts, source = select_power_source(None, None, None)
    assert (watts, source) == (STATIC_DEFAULT_WATTS, "default")


def test_zero_reading_treated_as_absent():
    """A 0W reading is a dropped sensor, not a real value — skip it so cost
    never pins at zero."""
    watts, source = select_power_source(0, 0, 90.0)
    assert (watts, source) == (90.0, "estimate")


# --- psu_watchdog_transition: return shape -------------------------------

def test_transition_returns_notes_and_streak():
    """The watchdog is stateful now: it returns (notes, new_degraded_streak)
    so the daemon can persist the consecutive-degraded counter across cycles."""
    notes, streak = psu_watchdog_transition("shelly", "shelly", 290)
    assert notes == []
    assert streak == 0


# --- psu_watchdog_transition: no false alarms ----------------------------

def test_no_alert_on_first_observation():
    notes, streak = psu_watchdog_transition(None, "shelly", 290)
    assert notes == []
    assert streak == 0


def test_first_observation_while_degraded_starts_streak_but_no_page():
    """A daemon booting into a degraded state starts counting but never pages
    on the very first cycle."""
    notes, streak = psu_watchdog_transition(
        None, "default", 150, degraded_streak=0, degraded_cycles_before_page=3
    )
    assert notes == []
    assert streak == 1


def test_no_alert_when_source_unchanged_primary():
    notes, streak = psu_watchdog_transition("shelly", "shelly", 290)
    assert notes == []
    assert streak == 0


# --- psu_watchdog_transition: debounce (the core of the 2026-07-12 fix) ---

def test_single_degraded_cycle_does_not_page():
    """A one-cycle drop to the floor (a momentary exporter-scrape miss) is
    silent — it just increments the streak. THIS is what stops the storm."""
    notes, streak = psu_watchdog_transition(
        "shelly", "default", 150, degraded_streak=0, degraded_cycles_before_page=3
    )
    assert notes == []
    assert streak == 1


def test_second_degraded_cycle_still_silent():
    notes, streak = psu_watchdog_transition(
        "default", "default", 150, degraded_streak=1, degraded_cycles_before_page=3
    )
    assert notes == []
    assert streak == 2


def test_pages_exactly_when_streak_reaches_threshold():
    """Sustained degradation (3rd consecutive cycle) crosses the threshold and
    pages once."""
    notes, streak = psu_watchdog_transition(
        "default", "default", 150, degraded_streak=2, degraded_cycles_before_page=3
    )
    assert len(notes) == 1
    assert notes[0]["severity"] == "critical"
    assert streak == 3


def test_no_repage_after_threshold_crossed():
    """Once we've paged, further degraded cycles are silent (no per-cycle
    spam) — the streak keeps climbing but emits nothing."""
    notes, streak = psu_watchdog_transition(
        "default", "default", 150, degraded_streak=3, degraded_cycles_before_page=3
    )
    assert notes == []
    assert streak == 4


def test_estimate_and_default_share_degraded_tier_and_accumulate():
    """estimate↔default both count as degraded — moving between them keeps
    accumulating the streak, still silent below threshold."""
    notes, streak = psu_watchdog_transition(
        "estimate", "default", 150, degraded_streak=1, degraded_cycles_before_page=3
    )
    assert notes == []
    assert streak == 2


def test_drop_straight_through_estimate_counts_as_one_degraded_cycle():
    notes, streak = psu_watchdog_transition(
        "shelly", "estimate", 250, degraded_streak=0, degraded_cycles_before_page=3
    )
    assert notes == []
    assert streak == 1


# --- psu_watchdog_transition: page message content -----------------------

def test_page_message_names_shelly_and_icue_not_hwinfo():
    """The retired HWiNFO must not appear — the page must point the operator at
    the Shelly plug and iCUE, the sources that actually feed this metric."""
    notes, _ = psu_watchdog_transition(
        "default", "default", 150, degraded_streak=2, degraded_cycles_before_page=3
    )
    msg = notes[0]["message"]
    assert "Shelly" in msg
    assert "iCUE" in msg
    assert "HWiNFO" not in msg


def test_page_message_distinguishes_estimate_from_floor():
    """On the software estimate the message says so; on the static floor it
    names the floor — so the operator knows how blind cost really is."""
    est_notes, _ = psu_watchdog_transition(
        "estimate", "estimate", 250, degraded_streak=2, degraded_cycles_before_page=3
    )
    floor_notes, _ = psu_watchdog_transition(
        "default", "default", 150, degraded_streak=2, degraded_cycles_before_page=3
    )
    assert "estimate" in est_notes[0]["message"].lower()
    assert "floor" in floor_notes[0]["message"].lower()


# --- psu_watchdog_transition: recovery + fallback heads-up ----------------

def test_recovery_to_primary_after_page_is_info():
    """We paged (streak >= threshold), then the meter came back → announce the
    recovery once, reset the streak."""
    notes, streak = psu_watchdog_transition(
        "default", "shelly", 290, degraded_streak=3, degraded_cycles_before_page=3
    )
    assert len(notes) == 1
    assert notes[0]["severity"] == "info"
    assert "recovered" in notes[0]["message"].lower()
    assert streak == 0


def test_recovery_from_blip_is_silent():
    """A degraded blip that never reached the page threshold recovers SILENTLY
    — no 'recovered' note, because there was nothing to recover from. This is
    what keeps the flapping from spamming Discord on the recovery side too."""
    notes, streak = psu_watchdog_transition(
        "default", "shelly", 290, degraded_streak=1, degraded_cycles_before_page=3
    )
    assert notes == []
    assert streak == 0


def test_primary_to_fallback_is_info_heads_up_not_page():
    """Meter dropped but the iCUE tap covers → a Discord heads-up (cost stays
    accurate), never a Telegram page."""
    notes, streak = psu_watchdog_transition(
        "shelly", "icue", 305, degraded_streak=0, degraded_cycles_before_page=3
    )
    assert len(notes) == 1
    assert notes[0]["severity"] == "info"
    assert "iCUE" in notes[0]["message"]
    assert streak == 0


def test_partial_recovery_to_fallback_after_page_is_info():
    """Recovering from a sustained outage to the iCUE fallback (meter still
    down) — an informational partial-recovery note."""
    notes, streak = psu_watchdog_transition(
        "default", "icue", 305, degraded_streak=3, degraded_cycles_before_page=3
    )
    assert len(notes) == 1
    assert notes[0]["severity"] == "info"
    assert streak == 0


def test_legacy_hx1500i_source_still_classifies_as_primary():
    """Backcompat: a brain_knowledge row left over from the HWiNFO era stores
    ``hx1500i``. It must still tier as primary so the label swap doesn't fire a
    spurious transition alert on the first post-deploy cycle."""
    # hx1500i (legacy primary) -> shelly (new primary): same tier, no alarm.
    notes, streak = psu_watchdog_transition("hx1500i", "shelly", 290)
    assert notes == []
    assert streak == 0


# --- fetch_icue_psu_watts ------------------------------------------------

@pytest.mark.asyncio
async def test_fetch_icue_returns_float_on_fresh_row():
    pool = MagicMock()
    pool.fetchrow = AsyncMock(return_value={"metric_value": 382.0})
    assert await fetch_icue_psu_watts(pool, 30) == 382.0


@pytest.mark.asyncio
async def test_fetch_icue_returns_none_when_no_fresh_sample():
    """Freshness gate returned nothing (dead tap / iCUE down) → None so the
    caller falls through to the estimate instead of billing on stale watts."""
    pool = MagicMock()
    pool.fetchrow = AsyncMock(return_value=None)
    assert await fetch_icue_psu_watts(pool, 30) is None


@pytest.mark.asyncio
async def test_fetch_icue_swallows_db_errors():
    pool = MagicMock()
    pool.fetchrow = AsyncMock(side_effect=RuntimeError("db down"))
    assert await fetch_icue_psu_watts(pool, 30) is None
