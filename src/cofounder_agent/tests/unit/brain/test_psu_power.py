"""Unit tests for ``brain/psu_power.py``.

Pins the electricity-cost wall-power priority chain (HWiNFO HX1500i →
iCUE CSV tap → software estimate → static floor) and the graduated
PSU-watchdog alert transitions. Added 2026-06-03 alongside wiring the
iCUE tap in as a real-power fallback: HWiNFO, AIDA64, and iCUE contend
over the SMBus/USB-HID, so HWiNFO drops the Corsair PSU intermittently
and the always-on iCUE tap covers for it (keeping cost accurate without
paging) — only losing BOTH sources pages the operator.
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

def test_hwinfo_psu_is_primary():
    watts, source = select_power_source(290.0, 305.0, 90.0)
    assert (watts, source) == (290.0, "hx1500i")


def test_icue_fallback_when_hwinfo_absent():
    """HWiNFO dropped (None) → use the iCUE tap value, not the estimate."""
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


# --- psu_watchdog_transition ---------------------------------------------

def test_no_alert_on_first_observation():
    assert psu_watchdog_transition(None, "hx1500i", 290) == []


def test_no_alert_when_source_unchanged():
    assert psu_watchdog_transition("hx1500i", "hx1500i", 290) == []


def test_no_alert_estimate_to_default_same_tier():
    """Both are 'degraded' quality — moving between them isn't worth a page."""
    assert psu_watchdog_transition("estimate", "default", 150) == []


def test_hwinfo_drop_to_icue_is_info_not_page():
    notes = psu_watchdog_transition("hx1500i", "icue", 305)
    assert len(notes) == 1
    assert notes[0]["severity"] == "info"
    assert "iCUE" in notes[0]["message"]


def test_drop_to_estimate_pages_critical():
    notes = psu_watchdog_transition("hx1500i", "estimate", 150)
    assert len(notes) == 1
    assert notes[0]["severity"] == "critical"


def test_icue_to_estimate_pages_critical():
    """Losing the fallback too means no real PSU data anywhere → page."""
    notes = psu_watchdog_transition("icue", "estimate", 150)
    assert notes[0]["severity"] == "critical"


def test_recovery_to_primary_is_info():
    notes = psu_watchdog_transition("estimate", "hx1500i", 290)
    assert len(notes) == 1
    assert notes[0]["severity"] == "info"
    assert "recovered" in notes[0]["message"].lower()


def test_partial_recovery_estimate_to_icue_is_info():
    notes = psu_watchdog_transition("estimate", "icue", 305)
    assert len(notes) == 1
    assert notes[0]["severity"] == "info"


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
