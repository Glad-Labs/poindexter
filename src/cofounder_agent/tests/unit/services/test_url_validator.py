"""
Unit tests for services/url_validator.py.

Covers URL extraction from markdown/HTML, cache behavior, internal domain
skipping, and async validation (with mocked HTTP).

Rewritten 2026-05-29 (#272 leaf batch 1) after the module was converted
from a module-level ``site_config`` singleton + ``get_url_validator()``
accessor to constructor DI. ``URLValidator`` now requires a ``site_config``
kwarg; tests construct ``SiteConfig(initial_config={...})`` directly.
"""

import asyncio
import time
from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest

from services.site_config import SiteConfig
from services.url_validator import URLValidator


@pytest.fixture
def validator():
    return URLValidator(
        site_config=SiteConfig(initial_config={"site_domain": "localhost:3000"}),
        timeout=2.0,
        cache_ttl=60,
    )


# ---------------------------------------------------------------------------
# extract_urls
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestExtractUrls:
    def test_empty_content_returns_empty(self, validator):
        assert validator.extract_urls("") == []
        assert validator.extract_urls(None) == []

    def test_extracts_markdown_links(self, validator):
        content = "Check out [Google](https://google.com) and [GitHub](https://github.com)."
        urls = validator.extract_urls(content)
        assert "https://google.com" in urls
        assert "https://github.com" in urls

    def test_extracts_bare_urls(self, validator):
        content = "Visit https://example.com for more info."
        urls = validator.extract_urls(content)
        assert "https://example.com" in urls

    def test_extracts_href_urls(self, validator):
        content = '<a href="https://example.com/page">link</a>'
        urls = validator.extract_urls(content)
        assert "https://example.com/page" in urls

    def test_deduplicates_urls(self, validator):
        content = "See https://dup.com and also https://dup.com again."
        urls = validator.extract_urls(content)
        assert urls.count("https://dup.com") == 1

    def test_strips_trailing_punctuation(self, validator):
        content = "Check https://example.com/path."
        urls = validator.extract_urls(content)
        assert "https://example.com/path" in urls
        assert not any(u.endswith(".") for u in urls)

    def test_skips_non_http_schemes(self, validator):
        content = "[mail](mailto:x@y.com) and [ftp](ftp://files.example.com)"
        urls = validator.extract_urls(content)
        assert len(urls) == 0

    def test_skips_internal_domains(self, validator):
        # _is_internal checks against _SKIP_DOMAINS which includes localhost
        content = "See https://external.com and http://localhost:3000/posts/1"
        urls = validator.extract_urls(content)
        assert "https://external.com" in urls
        # localhost should be skipped
        assert not any("localhost" in u for u in urls)

    def test_mixed_content(self, validator):
        content = """
# Blog Post

Check [Python docs](https://docs.python.org/3/) for details.
Also see https://numpy.org/doc/stable/ for arrays.

<a href="https://pandas.pydata.org/">Pandas</a> is great too.
"""
        urls = validator.extract_urls(content)
        assert len(urls) == 3
        assert "https://docs.python.org/3/" in urls
        assert "https://numpy.org/doc/stable/" in urls
        assert "https://pandas.pydata.org/" in urls


# ---------------------------------------------------------------------------
# Cache
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestCache:
    def test_cache_hit_returns_value(self, validator):
        validator._cache_set("https://cached.com", True, 200)
        assert validator._cache_get("https://cached.com") is True

    def test_cache_miss_returns_none(self, validator):
        assert validator._cache_get("https://uncached.com") is None

    def test_expired_entry_returns_none(self, validator):
        validator._cache["https://old.com"] = (True, 200, time.time() - 999)
        # TTL is 60s, entry is 999s old
        assert validator._cache_get("https://old.com") is None
        # Entry should be evicted
        assert "https://old.com" not in validator._cache

    def test_cache_stats(self, validator):
        validator._cache_set("https://a.com", True, 200)
        validator._cache["https://b.com"] = (False, 404, time.time() - 999)
        stats = validator.cache_stats()
        assert stats["total_cached"] == 2
        assert stats["active"] == 1  # only a.com is fresh


# ---------------------------------------------------------------------------
# validate_url (async, mocked HTTP)
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestValidateUrl:
    def _run(self, coro):
        return asyncio.run(coro)

    def test_returns_true_on_200(self, validator):
        mock_resp = MagicMock()
        mock_resp.status_code = 200

        mock_client = MagicMock()
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=None)
        mock_client.head = AsyncMock(return_value=mock_resp)

        with patch("services.url_validator.httpx.AsyncClient", return_value=mock_client):
            result = self._run(validator.validate_url("https://good.com"))
        assert result is True

    def test_returns_false_on_404(self, validator):
        mock_resp = MagicMock()
        mock_resp.status_code = 404

        mock_client = MagicMock()
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=None)
        mock_client.head = AsyncMock(return_value=mock_resp)

        with patch("services.url_validator.httpx.AsyncClient", return_value=mock_client):
            result = self._run(validator.validate_url("https://missing.com"))
        assert result is False

    def test_falls_back_to_get_on_405(self, validator):
        head_resp = MagicMock()
        head_resp.status_code = 405
        get_resp = MagicMock()
        get_resp.status_code = 200

        mock_client = MagicMock()
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=None)
        mock_client.head = AsyncMock(return_value=head_resp)
        mock_client.get = AsyncMock(return_value=get_resp)

        with patch("services.url_validator.httpx.AsyncClient", return_value=mock_client):
            result = self._run(validator.validate_url("https://no-head.com"))
        assert result is True
        mock_client.get.assert_called_once()

    def test_returns_false_on_timeout(self, validator):
        mock_client = MagicMock()
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=None)
        mock_client.head = AsyncMock(side_effect=httpx.TimeoutException("timeout"))

        with patch("services.url_validator.httpx.AsyncClient", return_value=mock_client):
            result = self._run(validator.validate_url("https://slow.com"))
        assert result is False

    def test_uses_cached_result(self, validator):
        validator._cache_set("https://cached.com", True, 200)

        # Should not make any HTTP call
        result = self._run(validator.validate_url("https://cached.com"))
        assert result is True


@pytest.mark.unit
class TestUserAgent:
    """``validate_url`` identifies with the shared crawler UA
    (``utils.crawler_ua.build_crawler_ua``) instead of the old
    ``{site_name}-LinkChecker/1.0`` spelling. Contactless by default (the
    OSS contact-URL leak guard); ``+contact`` when ``crawler_contact_url``
    is set. The ``Mozilla/5.0 (compatible; …)`` framing is what WAFs accept.
    Mirrors the ``TestUserAgent`` pattern in
    ``test_check_published_links_job``.
    """

    def _run(self, coro):
        return asyncio.run(coro)

    def _mock_client(self):
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_client = MagicMock()
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=None)
        mock_client.head = AsyncMock(return_value=mock_resp)
        return mock_client

    def test_sends_crawler_ua_contactless_by_default(self):
        validator = URLValidator(
            site_config=SiteConfig(initial_config={"site_domain": "localhost:3000"}),
            timeout=2.0,
        )
        mock_client = self._mock_client()
        with patch("services.url_validator.httpx.AsyncClient", return_value=mock_client):
            self._run(validator.validate_url("https://ok.example"))

        ua = mock_client.head.call_args.kwargs["headers"]["User-Agent"]
        # No crawler_contact_url → contact-less form (OSS leak guard).
        assert ua == "Mozilla/5.0 (compatible; PoindexterUrlValidator/1.0)"

    def test_user_agent_includes_contact_when_configured(self):
        validator = URLValidator(
            site_config=SiteConfig(initial_config={
                "site_domain": "localhost:3000",
                "crawler_contact_url": "https://gladlabs.io/bot",
            }),
            timeout=2.0,
        )
        mock_client = self._mock_client()
        with patch("services.url_validator.httpx.AsyncClient", return_value=mock_client):
            self._run(validator.validate_url("https://ok.example"))

        ua = mock_client.head.call_args.kwargs["headers"]["User-Agent"]
        assert ua == (
            "Mozilla/5.0 (compatible; PoindexterUrlValidator/1.0; "
            "+https://gladlabs.io/bot)"
        )


# ---------------------------------------------------------------------------
# validate_urls (batch)
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestValidateUrls:
    def _run(self, coro):
        return asyncio.run(coro)

    def test_empty_list_returns_empty_dict(self, validator):
        result = self._run(validator.validate_urls([]))
        assert result == {}

    def test_batch_validates_multiple(self, validator):
        mock_resp_200 = MagicMock()
        mock_resp_200.status_code = 200
        mock_resp_404 = MagicMock()
        mock_resp_404.status_code = 404

        async def mock_head(url, **kwargs):
            if "good" in url:
                return mock_resp_200
            return mock_resp_404

        mock_client = MagicMock()
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=None)
        mock_client.head = mock_head

        with patch("services.url_validator.httpx.AsyncClient", return_value=mock_client):
            result = self._run(
                validator.validate_urls(["https://good.com", "https://bad.com"])
            )
        assert result["https://good.com"] == "valid"
        assert result["https://bad.com"] == "invalid"


# ---------------------------------------------------------------------------
# Construction (constructor DI — #272 leaf batch 1)
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestConstruction:
    def test_site_config_is_required(self):
        """No default — passing nothing must raise per fail-loud principle."""
        with pytest.raises(TypeError):
            URLValidator()  # type: ignore[call-arg]

    def test_skip_domains_reads_site_config(self):
        """``_skip_domains`` resolves the operator's site_domain at call time."""
        v = URLValidator(site_config=SiteConfig(initial_config={"site_domain": "mybrand.io"}))
        skip = v._skip_domains()
        assert "mybrand.io" in skip
        assert "www.mybrand.io" in skip
        assert "localhost" in skip

    def test_is_internal_uses_injected_config(self):
        v = URLValidator(site_config=SiteConfig(initial_config={"site_domain": "mybrand.io"}))
        assert v._is_internal("https://mybrand.io/posts/1") is True
        assert v._is_internal("https://external.com/x") is False


# ---------------------------------------------------------------------------
# Container wiring (#272: cached_property on AppContainer)
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestAppContainerWiring:
    """``AppContainer.url_validator`` returns a memoised URLValidator."""

    def test_app_container_exposes_url_validator(self):
        from services.container import AppContainer

        site_config = SiteConfig(initial_config={"site_domain": "wired.example"})
        container = AppContainer(site_config=site_config, pool=MagicMock())

        v = container.url_validator
        assert isinstance(v, URLValidator)
        # Reads through to the container's SiteConfig.
        assert "wired.example" in v._skip_domains()

    def test_cached_property_memoises(self):
        from services.container import AppContainer

        container = AppContainer(site_config=SiteConfig(), pool=MagicMock())
        assert container.url_validator is container.url_validator
