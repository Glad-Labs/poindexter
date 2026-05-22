"""
Tests for ``services.citation_verifier._build_citation_headers``.

Focused on the poindexter#485 follow-up: the crawler User-Agent string
must NOT hardcode ``gladlabs.io`` (or any operator-specific URL). The
``+url`` portion of the standard crawler-UA convention now comes from
``app_settings.crawler_contact_url`` — empty means the portion is
omitted entirely, so OSS forks identify themselves as
``PoindexterCitationVerifier`` without leaking the source operator's
contact info.
"""

import pytest

from services.citation_verifier import (
    _build_citation_headers,
    set_site_config,
)
from services.site_config import SiteConfig


@pytest.mark.unit
class TestCitationHeaders:
    def test_no_contact_url_omits_plus_portion(self):
        """OSS default: ``crawler_contact_url`` unset → User-Agent
        ends at the version string, no ``+url`` tail. The previous
        behaviour leaked ``+https://gladlabs.io)`` into every OSS
        install's HEAD requests.
        """
        set_site_config(SiteConfig())

        headers = _build_citation_headers()

        assert headers["User-Agent"] == (
            "Mozilla/5.0 (compatible; PoindexterCitationVerifier/1.0)"
        )
        # The exact leak pattern the helper was added to close:
        assert "gladlabs.io" not in headers["User-Agent"]
        assert "+" not in headers["User-Agent"]

    def test_contact_url_appears_in_user_agent(self):
        """Operator-set ``crawler_contact_url`` lands in the
        ``+<url>`` portion of the User-Agent. Site admins receiving
        traffic from this crawler can reach the operator at that URL.
        """
        set_site_config(SiteConfig(initial_config={
            "crawler_contact_url": "https://example.org/contact",
        }))

        headers = _build_citation_headers()

        assert headers["User-Agent"] == (
            "Mozilla/5.0 (compatible; PoindexterCitationVerifier/1.0; "
            "+https://example.org/contact)"
        )

    def test_contact_url_whitespace_stripped(self):
        """Whitespace-only or blank-after-strip values are treated as
        empty. Otherwise an operator who typed a trailing newline in
        OpenClaw would emit ``+   )`` as the UA tail.
        """
        set_site_config(SiteConfig(initial_config={
            "crawler_contact_url": "   ",
        }))

        headers = _build_citation_headers()

        assert "+" not in headers["User-Agent"]

    def test_site_config_read_failure_falls_back_to_no_contact(self):
        """A misbehaving SiteConfig (DB pool exhausted, missing
        column) doesn't break HEAD probing — falls back to the
        no-contact UA. The citation verifier is a quality signal,
        not a critical path, so a missing UA contact never blocks.
        """

        class _BrokenSC:
            def get(self, key, default=""):
                raise RuntimeError("simulated SiteConfig failure")

        set_site_config(_BrokenSC())  # type: ignore[arg-type]
        try:
            headers = _build_citation_headers()
            assert headers["User-Agent"] == (
                "Mozilla/5.0 (compatible; PoindexterCitationVerifier/1.0)"
            )
            assert headers["Accept"] == "*/*"
        finally:
            # Reset so other tests don't see the broken stub.
            set_site_config(SiteConfig())

    def test_accept_header_always_wildcard(self):
        set_site_config(SiteConfig())
        assert _build_citation_headers()["Accept"] == "*/*"
        set_site_config(SiteConfig(initial_config={
            "crawler_contact_url": "https://example.com",
        }))
        assert _build_citation_headers()["Accept"] == "*/*"
