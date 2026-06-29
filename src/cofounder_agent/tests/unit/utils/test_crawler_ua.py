"""Unit tests for ``utils.crawler_ua.build_crawler_ua``.

The shared crawler User-Agent builder. The critical contract is the OSS
contact-URL leak guard: when ``app_settings.crawler_contact_url`` is unset
the ``; +<url>`` portion MUST be omitted so forks don't ship the source
operator's contact URL as a baked-in default
(``feedback_no_operator_info_to_public_repo``).
"""

from __future__ import annotations

from services.site_config import SiteConfig
from utils.crawler_ua import build_crawler_ua


class TestContactLeakGuard:
    def test_unset_contact_omits_plus_url(self):
        """OSS default: crawler_contact_url unset → no ``+url`` suffix."""
        sc = SiteConfig(initial_config={})
        ua = build_crawler_ua(sc, product="PoindexterLinkCheck")
        assert ua == "Mozilla/5.0 (compatible; PoindexterLinkCheck/1.0)"
        assert "+" not in ua  # no contact leaked

    def test_empty_string_contact_omits_plus_url(self):
        sc = SiteConfig(initial_config={"crawler_contact_url": ""})
        ua = build_crawler_ua(sc, product="PoindexterLinkCheck")
        assert ua == "Mozilla/5.0 (compatible; PoindexterLinkCheck/1.0)"

    def test_whitespace_only_contact_treated_as_unset(self):
        sc = SiteConfig(initial_config={"crawler_contact_url": "   \t \n"})
        ua = build_crawler_ua(sc, product="PoindexterLinkCheck")
        assert ua == "Mozilla/5.0 (compatible; PoindexterLinkCheck/1.0)"

    def test_set_contact_appends_plus_url(self):
        sc = SiteConfig(initial_config={"crawler_contact_url": "https://example.org/bot"})
        ua = build_crawler_ua(sc, product="PoindexterLinkCheck")
        assert ua == (
            "Mozilla/5.0 (compatible; PoindexterLinkCheck/1.0; "
            "+https://example.org/bot)"
        )

    def test_contact_is_stripped(self):
        sc = SiteConfig(initial_config={
            "crawler_contact_url": "  https://example.org/bot  ",
        })
        ua = build_crawler_ua(sc, product="PoindexterLinkCheck")
        assert ua.endswith("+https://example.org/bot)")


class TestProductAndVersion:
    def test_product_token_appears(self):
        sc = SiteConfig(initial_config={})
        ua = build_crawler_ua(sc, product="PoindexterCitationVerifier")
        assert "PoindexterCitationVerifier/1.0" in ua

    def test_custom_version(self):
        sc = SiteConfig(initial_config={})
        ua = build_crawler_ua(sc, product="PoindexterLinkCheck", version="2.3")
        assert ua == "Mozilla/5.0 (compatible; PoindexterLinkCheck/2.3)"

    def test_browser_ish_compatible_framing(self):
        """The UA keeps the ``Mozilla/5.0 (compatible; …)`` framing that
        WAFs accept — the whole point of not shipping the bare httpx UA."""
        sc = SiteConfig(initial_config={})
        ua = build_crawler_ua(sc, product="X")
        assert ua.startswith("Mozilla/5.0 (compatible; ")


class TestNoneSafety:
    def test_none_site_config_yields_contactless_ua(self):
        """Standalone/test callers without a SiteConfig still get a valid UA."""
        ua = build_crawler_ua(None, product="PoindexterLinkCheck")
        assert ua == "Mozilla/5.0 (compatible; PoindexterLinkCheck/1.0)"

    def test_site_config_get_raising_is_tolerated(self):
        class _Boom:
            def get(self, *_a, **_k):
                raise RuntimeError("config exploded")

        ua = build_crawler_ua(_Boom(), product="PoindexterLinkCheck")
        # Defensive: a broken config degrades to the contact-less UA, not a crash.
        assert ua == "Mozilla/5.0 (compatible; PoindexterLinkCheck/1.0)"


class TestCitationVerifierParity:
    """The helper must reproduce citation_verifier's exact historical UA so
    its byte-for-byte header tests keep passing after the refactor."""

    def test_matches_citation_verifier_unset(self):
        sc = SiteConfig(initial_config={})
        ua = build_crawler_ua(sc, product="PoindexterCitationVerifier")
        assert ua == "Mozilla/5.0 (compatible; PoindexterCitationVerifier/1.0)"

    def test_matches_citation_verifier_set(self):
        sc = SiteConfig(initial_config={
            "crawler_contact_url": "https://example.org/contact",
        })
        ua = build_crawler_ua(sc, product="PoindexterCitationVerifier")
        assert ua == (
            "Mozilla/5.0 (compatible; PoindexterCitationVerifier/1.0; "
            "+https://example.org/contact)"
        )
