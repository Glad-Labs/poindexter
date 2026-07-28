"""Unit tests for the self-reference gate (services/topic_self_reference.py).

Regression cover for #925: batch 6322bd8b ranked the operator's own homepage
as its #1 external topic candidate, and ten self-referential rows had
accumulated in topic_pool.
"""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from services.topic_self_reference import (
    EXCLUDED_DOMAINS_KEY,
    is_self_referential,
    normalize_host,
    resolve_owned_hosts,
)


class _StubConfig:
    """Minimal dict-backed SiteConfig stand-in."""

    def __init__(self, values: dict[str, str], *, raise_on: str | None = None):
        self._values = values
        self._raise_on = raise_on

    def get(self, key, default=None):
        if key == self._raise_on:
            raise RuntimeError(f"settings backend exploded reading {key}")
        return self._values.get(key, default)


class TestNormalizeHost:
    @pytest.mark.parametrize("raw,expected", [
        ("https://www.gladlabs.io/", "gladlabs.io"),
        ("https://gladlabs.io/posts/some-slug", "gladlabs.io"),
        ("http://GladLabs.IO", "gladlabs.io"),
        ("gladlabs.io", "gladlabs.io"),            # bare host, no scheme
        ("www.gladlabs.io", "gladlabs.io"),
        ("https://gladlabs.io:8443/x", "gladlabs.io"),
        ("  https://gladlabs.io  ", "gladlabs.io"),
        ("https://blog.gladlabs.io", "blog.gladlabs.io"),
    ])
    def test_normalizes(self, raw, expected):
        assert normalize_host(raw) == expected

    @pytest.mark.parametrize("raw", ["", "   ", None])
    def test_empty_inputs_yield_empty(self, raw):
        assert normalize_host(raw) == ""

    @pytest.mark.parametrize("raw", [MagicMock(), 42, object(), ["a"]])
    def test_non_string_never_coerced_into_a_host(self, raw):
        """A stub/mock settings value must switch the gate off, not invent a host.

        str(MagicMock()) parses to a plausible-looking hostname; coercing it
        would add a junk 'owned host' that could start matching real URLs.
        """
        assert normalize_host(raw) == ""

    def test_malformed_url_is_unknown_not_a_crash(self):
        # Unparseable IPv6 literal — must degrade to "unknown", never raise.
        assert normalize_host("http://[oops") == ""


class TestResolveOwnedHosts:
    def test_derives_from_site_url(self):
        cfg = _StubConfig({"site_url": "https://www.gladlabs.io"})
        assert resolve_owned_hosts(cfg) == frozenset({"gladlabs.io"})

    def test_merges_both_site_keys_and_dedups(self):
        cfg = _StubConfig({
            "site_url": "https://www.gladlabs.io",
            "public_site_url": "https://gladlabs.io",
        })
        assert resolve_owned_hosts(cfg) == frozenset({"gladlabs.io"})

    def test_extra_domains_from_setting(self):
        cfg = _StubConfig({
            "site_url": "https://gladlabs.io",
            EXCLUDED_DOMAINS_KEY: "gladlabs.ai, www.example.net",
        })
        assert resolve_owned_hosts(cfg) == frozenset(
            {"gladlabs.io", "gladlabs.ai", "example.net"}
        )

    def test_blank_entries_in_csv_ignored(self):
        cfg = _StubConfig({
            "site_url": "https://gladlabs.io",
            EXCLUDED_DOMAINS_KEY: " , ,, ",
        })
        assert resolve_owned_hosts(cfg) == frozenset({"gladlabs.io"})

    def test_none_config_yields_empty(self):
        assert resolve_owned_hosts(None) == frozenset()

    def test_config_without_get_yields_empty(self):
        assert resolve_owned_hosts(object()) == frozenset()

    def test_unconfigured_site_url_yields_empty(self):
        assert resolve_owned_hosts(_StubConfig({})) == frozenset()

    def test_raising_backend_does_not_disable_the_other_key(self):
        # site_url explodes; public_site_url must still be picked up, so a
        # flaky settings read can't silently switch the whole gate off.
        cfg = _StubConfig(
            {"public_site_url": "https://gladlabs.io"}, raise_on="site_url",
        )
        assert resolve_owned_hosts(cfg) == frozenset({"gladlabs.io"})


class TestIsSelfReferential:
    OWNED = frozenset({"gladlabs.io"})

    @pytest.mark.parametrize("url", [
        "https://www.gladlabs.io/",                      # the #1 candidate
        "https://www.gladlabs.io/product",
        "https://www.gladlabs.io/posts",
        "https://www.gladlabs.io/category/technology",
        "https://www.gladlabs.io/posts/glad-labs-one-person-indie-shop-07217583",
        "https://gladlabs.io",
        "https://blog.gladlabs.io/entry",                # subdomain
    ])
    def test_owned_urls_rejected(self, url):
        assert is_self_referential(url, self.OWNED) is True

    @pytest.mark.parametrize("url", [
        "https://news.ycombinator.com/item?id=1",
        "https://dev.to/someone/post",
        "https://xiyanghu.github.io/lab/",
    ])
    def test_third_party_urls_kept(self, url):
        assert is_self_referential(url, self.OWNED) is False

    def test_lookalike_suffix_not_matched(self):
        # The dot guard: "notgladlabs.io" endswith "gladlabs.io" as a string.
        assert is_self_referential("https://notgladlabs.io/x", self.OWNED) is False

    @pytest.mark.parametrize("url", ["", None])
    def test_urlless_candidates_pass_through(self, url):
        # knowledge / internal_rag sources yield no URL — must not be dropped.
        assert is_self_referential(url, self.OWNED) is False

    def test_no_owned_hosts_disables_the_gate(self):
        assert is_self_referential("https://gladlabs.io", frozenset()) is False

    def test_brand_property_on_third_party_host_needs_explicit_config(self):
        """Documents the known limitation stated in the module docstring."""
        assert is_self_referential("https://x.com/_gladlabs", self.OWNED) is False
        # ...unless the operator lists the host explicitly.
        assert is_self_referential(
            "https://x.com/_gladlabs", frozenset({"gladlabs.io", "x.com"}),
        ) is True
