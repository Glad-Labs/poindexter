"""Phase-2 seo_refresh settings must be seeded (SEO Harvest Loop #763)."""

from services.settings_defaults import DEFAULTS


def test_seo_refresh_settings_seeded():
    assert DEFAULTS["seo.refresh.scope"] == "meta_only"
    # Approval-FIRST — the gate ships ENABLED (re-publishing a live post pauses
    # for sign-off), unlike draft_gate which ships disabled.
    assert DEFAULTS["pipeline_gate_seo_refresh_gate"] == "true"
    assert "seo.refresh.auto_publish_after_clean_runs" in DEFAULTS
    # The feature is still default-off at the enqueue layer.
    assert DEFAULTS["seo.refresh.enabled"] == "false"


def test_no_seo_refresh_default_is_empty():
    # app_settings.value is NOT NULL; '' is the unset sentinel and would crash CI.
    for key, value in DEFAULTS.items():
        if key.startswith("seo.refresh.") or key == "pipeline_gate_seo_refresh_gate":
            assert value != "", f"{key} must have a non-empty default"
