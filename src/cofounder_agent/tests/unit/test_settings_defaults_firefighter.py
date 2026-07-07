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


def test_firefighter_llm_longtail_defaults_present_and_typed():
    """Plan B (LLM long-tail) selector knobs. The selector runs on the worker via
    dispatch_complete, so the model carries the ``ollama/`` litellm prefix like
    its sibling ``ops_triage_writer_model``."""
    assert DEFAULTS["ops_firefighter_model"] == "ollama/llama3.2:3b"
    assert DEFAULTS["ops_firefighter_min_repeats"] == "2"
    assert DEFAULTS["ops_firefighter_min_confidence"] == "0.6"
    # int / float consumers parse the string forms.
    assert int(DEFAULTS["ops_firefighter_min_repeats"]) >= 1
    assert 0.0 < float(DEFAULTS["ops_firefighter_min_confidence"]) <= 1.0
