"""Guards the affiliate-injection settings defaults (dark-launched)."""

from services.settings_defaults import DEFAULTS


def test_affiliate_defaults_present_and_dark_launched():
    assert DEFAULTS["affiliate_injection_enabled"] == "false"
    assert DEFAULTS["affiliate_max_links_per_post"] == "3"
    assert DEFAULTS["affiliate_redirect_base_url"] == "/go"
    assert DEFAULTS["affiliate_disclosure_text"].strip() != ""
    assert DEFAULTS["plugin.job.sync_affiliate_clicks.enabled"] == "true"
    assert DEFAULTS["plugin.job.sync_affiliate_clicks.interval_seconds"] == "300"


def test_affiliate_defaults_are_strings():
    # app_settings values are always strings ('' is the unset sentinel, never NULL).
    for key in (
        "affiliate_injection_enabled",
        "affiliate_max_links_per_post",
        "affiliate_redirect_base_url",
        "affiliate_disclosure_text",
    ):
        assert isinstance(DEFAULTS[key], str)
