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


def test_max_per_run_default_seeded():
    # Phase 2b auto-enqueue cap (#763). The job schedules are auto-persisted by
    # PluginScheduler from the job class `schedule` attribute, so they are NOT in
    # DEFAULTS — only this tunable is.
    assert DEFAULTS["seo.refresh.max_per_run"] == "3"


def test_no_seo_refresh_default_is_empty():
    # app_settings.value is NOT NULL; '' is the unset sentinel and would crash CI.
    for key, value in DEFAULTS.items():
        if key.startswith("seo.refresh.") or key == "pipeline_gate_seo_refresh_gate":
            assert value != "", f"{key} must have a non-empty default"


def test_seo_refresh_findings_route_to_discord():
    # The queued-for-sign-off + outcome-measured findings are routine operator
    # notifications (Discord, not Telegram) per feedback_telegram_vs_discord.
    # delivery='discord' pins the channel; min_severity must admit the 'warn'
    # severity the jobs emit at (the router floors out 'info' at the SQL layer,
    # so 'warn' is the load-bearing contract — see the job tests).
    for kind, cooldown in (
        ("seo_refresh_queued", "360"),
        ("seo_refresh_outcome", "1440"),
    ):
        assert DEFAULTS[f"findings.{kind}.delivery"] == "discord"
        assert DEFAULTS[f"findings.{kind}.fallback"] == "log_only"
        assert DEFAULTS[f"findings.{kind}.min_severity"] == "warn"
        assert DEFAULTS[f"findings.{kind}.cooldown_minutes"] == cooldown
