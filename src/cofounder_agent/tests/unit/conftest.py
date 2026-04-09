"""
Shared fixtures for all unit tests.

Populates site_config with test brand values so tests don't fail
when code calls site_config.require() or site_config.get() for brand keys.
"""

from services.site_config import site_config

# Populate brand values for tests — these are the minimum required keys
_TEST_BRAND_CONFIG = {
    "site_name": "Test Site",
    "site_url": "https://www.test-site.example.com",
    "site_domain": "test-site.example.com",
    "company_name": "Test Company, LLC",
    "support_email": "hello@test.example.com",
    "privacy_email": "privacy@test.example.com",
    "newsletter_email": "news@test.example.com",
    "podcast_name": "Test Podcast",
    "podcast_description": "A test podcast feed",
    "video_feed_name": "Test Video",
    "site_title": "Test Site",
    "site_tagline": "Testing",
    "site_description": "Test site description",
    "owner_name": "Tester",
    "owner_email": "owner@test.example.com",
}

for key, value in _TEST_BRAND_CONFIG.items():
    site_config._config.setdefault(key, value)
