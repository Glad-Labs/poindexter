from services.settings_defaults import DEFAULTS


def test_firefighter_defaults_present_and_typed():
    assert DEFAULTS["ops_firefighter_enabled"] == "true"
    assert DEFAULTS["ops_firefighter_max_attempts_per_window"] == "3"
    assert DEFAULTS["ops_firefighter_window_minutes"] == "60"
    assert DEFAULTS["ops_firefighter_verify_after_seconds"] == "120"
    assert DEFAULTS["ops_firefighter_max_actions_per_hour"] == "10"
    # Empty CSV = "all registered actions allowed" (not NULL — value_not_null rule)
    assert DEFAULTS["ops_firefighter_action_allowlist"] == ""
    # All values are strings (app_settings.value is TEXT)
    for k in [k for k in DEFAULTS if k.startswith("ops_firefighter_")]:
        assert isinstance(DEFAULTS[k], str)
