import pytest

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
    assert DEFAULTS["ops_firefighter_model"] == "ollama/granite4.2:3b"
    assert DEFAULTS["ops_firefighter_min_repeats"] == "2"
    assert DEFAULTS["ops_firefighter_min_confidence"] == "0.6"
    # int / float consumers parse the string forms.
    assert int(DEFAULTS["ops_firefighter_min_repeats"]) >= 1
    assert 0.0 < float(DEFAULTS["ops_firefighter_min_confidence"]) <= 1.0


def test_firefighter_llm_longtail_engine_gate_defaults():
    """Plan B engine gates seeded: long-tail master switch, persistence age, and
    the circular-dependency exclusion regex (the LLM must never be asked to fix
    the substrate it runs on — spec's circular-dependency guard)."""
    import re

    assert DEFAULTS["ops_firefighter_llm_longtail_enabled"] == "true"
    assert DEFAULTS["ops_firefighter_min_age_minutes"] == "10"
    assert int(DEFAULTS["ops_firefighter_min_age_minutes"]) >= 0
    excl = DEFAULTS["ops_firefighter_llm_exclude_regex"]
    assert excl  # non-empty (value_not_null discipline)
    # It actually matches the LLM's own substrate so those alerts stay rule-only.
    assert re.search(excl, "OllamaUnresponsive")
    assert re.search(excl, "GPUTempHigh")


@pytest.mark.parametrize("key", ["ops_firefighter_model", "ops_triage_writer_model"])
def test_ops_model_defaults_are_permissively_licensed(key):
    """These two defaults ship publicly — they are the OSS product's pins.

    Neither ``services/settings_defaults.py`` nor
    ``services/migrations/0000_baseline.seeds.sql`` is stripped by
    ``scripts/sync-to-github.sh``, so whatever is seeded here is what a fresh
    Glad-Labs/poindexter install runs. The 2026-08-27 license audit initially
    concluded ``scripts/ops_sessions/_common.py`` held the last non-permissive
    default and missed both of these — the same Llama 3.2 Community License
    (not OSI-approved, acceptable-use policy, 700M-MAU ceiling, ``gated:
    manual`` upstream weights a downstream user cannot fetch) was still
    shipping from app_settings. This test is the guard that would have caught
    it, mirroring
    ``tests/unit/scripts/test_ops_common.py::test_default_model_pins_are_permissively_licensed``.

    Widen the allowlist only with an Apache-2.0/MIT model — verify with
    ``ollama show --license <tag>`` rather than assuming, and never re-admit a
    community license. Note the sibling size constraint documented at each key:
    these run alongside wan + image-gen, so a permissive-but-huge pin trades a
    licensing bug for a VRAM one.
    """
    from services.settings_defaults import DEFAULTS

    permissive = {
        "ollama/granite4.2:3b",  # IBM Granite 4.2 — Apache-2.0
        "ollama/granite4.2:8b",  # Apache-2.0
        "ollama/qwen2.5:7b",     # Qwen2.5 (7B) — Apache-2.0
        "ollama/phi4:14b",       # MIT
    }
    assert DEFAULTS[key] in permissive
