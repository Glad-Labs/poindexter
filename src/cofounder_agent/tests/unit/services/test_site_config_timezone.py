from __future__ import annotations

from services.site_config import SiteConfig


def test_timezone_from_config():
    cfg = SiteConfig(initial_config={"operator_timezone": "America/New_York"})
    assert cfg.timezone.key == "America/New_York"


def test_timezone_defaults_to_utc_when_absent():
    cfg = SiteConfig(initial_config={})
    assert cfg.timezone.key == "UTC"
