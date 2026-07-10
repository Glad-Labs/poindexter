"""Guard: the beacon bot-flag tunables ship as app_settings defaults."""
from __future__ import annotations

from services.settings_defaults import DEFAULTS


def test_beacon_bot_flag_defaults_present():
    assert DEFAULTS["beacon_bot_flag_enabled"] == "true"
    assert DEFAULTS["beacon_flood_window_hours"] == "24"
    assert DEFAULTS["beacon_flood_cap_per_window"] == "20"
    assert DEFAULTS["beacon_flood_backfill_cap"] == "30"


def test_beacon_bot_flag_defaults_are_strings():
    # app_settings values are always strings; '' is the unset sentinel, never NULL.
    for key in (
        "beacon_bot_flag_enabled",
        "beacon_flood_window_hours",
        "beacon_flood_cap_per_window",
        "beacon_flood_backfill_cap",
    ):
        assert isinstance(DEFAULTS[key], str)
