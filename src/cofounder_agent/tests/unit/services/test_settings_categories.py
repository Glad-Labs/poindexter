"""Unit tests for the app_settings category resolver.

The completeness gate runs over ``settings_defaults.DEFAULTS`` — the in-repo
public keyspace — rather than a snapshot of a live operator DB, so it stays
current automatically (a new key in DEFAULTS is checked immediately) and never
carries operator-overlay key names into the public mirror. Operator-only keys
(absent from DEFAULTS) still resolve at runtime via the same prefix rules; the
boot reconcile categorizes them on Matt's DB.
"""

import pytest

from services.settings_categories import (
    CATEGORIES,
    CATEGORY_IDS,
    resolve_category,
)
from services.settings_defaults import DEFAULTS


def test_thirteen_canonical_categories():
    ids = [c["id"] for c in CATEGORIES]
    assert len(ids) == 13
    assert len(set(ids)) == 13  # no dupes
    assert "general" not in ids  # general is the fallback, not a category
    # order is contiguous 1..13
    assert sorted(c["order"] for c in CATEGORIES) == list(range(1, 14))


@pytest.mark.parametrize(
    "key,expected",
    [
        ("findings_alert_route_watermark", "observability"),
        ("findings.broken_link.delivery", "observability"),
        ("qa_overall_score_threshold", "quality"),
        ("self_consistency_threshold", "quality"),
        ("plugin.llm_provider.primary.free", "plugins"),
        ("plugin.job.db_backup", "plugins"),
        ("compose_drift_auto_recover_enabled", "infrastructure"),
        ("compose_project_name", "infrastructure"),
        ("daily_spend_limit_usd", "cost"),
        ("daily_post_limit", "pipeline"),
        ("auto_publish_threshold", "pipeline"),
        ("dev_diary_auto_publish_threshold", "pipeline"),
        ("auto_embed_watch_enabled", "content"),
        ("monthly_spend_limit_usd", "cost"),  # monthly_spend prefix rule
        ("resend_api_key", "integrations"),  # secret -> subsystem
        ("anthropic_api_key", "models"),  # secret -> subsystem
        ("grafana_api_key", "observability"),
        ("mcp_http_probe_enabled", "self_healing"),
        ("mcp_oauth_client_id", "integrations"),
        ("vision_alt_model", "media"),
        ("site_name", "identity"),
        ("company_name", "identity"),
        ("affiliate_injection_enabled", "cost"),  # affiliate = monetization
        ("affiliate_redirect_base_url", "cost"),
        ("affiliate_clicks_last_sync", "cost"),  # runtime watermark, not in DEFAULTS
        # Media-lane watchdogs + recovery jobs (2026-08-15): subsystem-first,
        # like compose_drift -> infrastructure.
        ("narration_failure_probe_enabled", "media"),
        ("hero_fallback_window_hours", "media"),
        ("backfill_media_scripts_batch", "media"),
        ("backfill_video_shot_lists_enabled", "media"),
    ],
)
def test_anchor_mappings(key, expected):
    assert resolve_category(key) == expected


def test_determinism():
    assert resolve_category("qa_x") == resolve_category("qa_x")


def test_every_default_key_resolves_to_a_canonical_id():
    for k in DEFAULTS:
        assert resolve_category(k) in (CATEGORY_IDS | {"general"})


def test_general_residual_is_small():
    """The forcing function: a new key that matches no rule fails CI here."""
    residual = sorted(k for k in DEFAULTS if resolve_category(k) == "general")
    assert len(residual) <= 20, f"{len(residual)} keys fell to general: {residual}"
