"""operator_timezone: OSS default is UTC; operator overlay is America/New_York."""
from __future__ import annotations

from services.settings_defaults import DEFAULTS


def test_oss_default_is_utc():
    assert DEFAULTS["operator_timezone"] == "UTC"


def test_operator_overlay_sets_real_zone():
    # The overlay module is stripped from the public mirror; import guarded.
    from services.operator_overrides import OPERATOR_SETTING_OVERRIDES

    assert OPERATOR_SETTING_OVERRIDES["operator_timezone"] == "America/New_York"


def test_public_default_carries_no_operator_location():
    # Belt-and-suspenders against the no-operator-info-in-public rule.
    assert "America" not in DEFAULTS["operator_timezone"]
