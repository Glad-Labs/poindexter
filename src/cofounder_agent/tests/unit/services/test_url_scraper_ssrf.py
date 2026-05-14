"""SSRF guard tests for ``services.url_scraper``.

Audit punch list: docs/security/audit-2026-05-12.md P0 #5.

These tests pin the contract for the IP-denylist layer that runs before
every HTTP request and after every redirect:

- 127.0.0.0/8 loopback, ``localhost``, ``::1``               — blocked
- 10.0.0.0/8, 172.16.0.0/12, 192.168.0.0/16 RFC 1918         — blocked
- 169.254.0.0/16 link-local + cloud metadata (169.254.169.254) — blocked
- 100.64.0.0/10 CGNAT / Tailscale                            — blocked
- public IPs (1.1.1.1, 8.8.8.8)                              — allowed
- redirect chain whose final hop lands at 127.0.0.1          — blocked
- override flag ``url_scraper_allow_internal_ips=true``      — allows

All DNS + HTTP is mocked — no real network access required.
"""

from __future__ import annotations

import socket
from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest

from services import url_scraper
from services.url_scraper import (
    MAX_REDIRECTS,
    SSRFBlockedError,
    URLScrapeError,
    _is_blocked_ip,
    _resolve_and_check,
    _safe_get,
)


# ---------------------------------------------------------------------------
# Fakes
# ---------------------------------------------------------------------------


class _FakeResponse:
    def __init__(
        self,
        text: str = "",
        status_code: int = 200,
        headers: dict | None = None,
        is_success: bool = True,
    ):
        self.text = text
        self.status_code = status_code
        self.headers = headers or {}
        self.is_success = is_success

    def raise_for_status(self):
        if not self.is_success:
            raise httpx.HTTPStatusError("boom", request=MagicMock(), response=MagicMock())


class _FakeAsyncClient:
    def __init__(self, responses):
        self._responses = responses
        self.requested_urls: list[str] = []

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, tb):
        return False

    async def get(self, url: str, **_kwargs):
        self.requested_urls.append(url)
        if callable(self._responses):
            return self._responses(url)
        if not self._responses:
            raise AssertionError(f"unexpected GET {url}")
        return self._responses.pop(0)


def _site_config(values: dict | None = None) -> MagicMock:
    """Build a mock SiteConfig that responds to .get / .get_bool."""
    sc = MagicMock()
    values = values or {}

    def _get(key, default=""):
        return values.get(key, default)

    def _get_bool(key, default=False):
        raw = values.get(key, default)
        if isinstance(raw, bool):
            return raw
        return str(raw).lower() in ("true", "1", "yes", "on")

    sc.get.side_effect = _get
    sc.get_bool.side_effect = _get_bool
    return sc


def _patch_site_config(monkeypatch, values: dict | None = None):
    monkeypatch.setattr("services.url_scraper.site_config", _site_config(values))


def _mock_getaddrinfo(monkeypatch, mapping: dict[str, list[str]]):
    """Patch socket.getaddrinfo so *hostname → list of IPs* drives resolution.

    Anything outside the mapping raises ``socket.gaierror`` so we never
    accidentally hit the live resolver in a unit test.
    """

    def _fake(host, port, *args, **kwargs):
        if host in mapping:
            results = []
            for ip in mapping[host]:
                family = socket.AF_INET6 if ":" in ip else socket.AF_INET
                results.append(
                    (family, socket.SOCK_STREAM, socket.IPPROTO_TCP, "", (ip, port)),
                )
            if results:
                return results
        raise socket.gaierror(f"no fake mapping for {host}")

    monkeypatch.setattr("services.url_scraper.socket.getaddrinfo", _fake)


# ---------------------------------------------------------------------------
# _is_blocked_ip — pure helper
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestIsBlockedIp:
    @pytest.mark.parametrize(
        "ip",
        [
            "127.0.0.1",
            "127.255.255.254",
            "10.0.0.1",
            "10.255.255.255",
            "172.16.0.1",
            "172.31.255.255",
            "192.168.0.1",
            "192.168.1.100",
            "169.254.169.254",  # cloud metadata
            "169.254.0.1",
            "100.64.0.1",       # Tailscale CGNAT
            "100.127.255.254",
            "0.0.0.0",
            "::1",
            "fe80::1",
            "fc00::1",
            "fd00::beef",        # ULA inside fc00::/7
            "::",
            "::ffff:127.0.0.1",  # IPv4-mapped loopback
            "::ffff:10.0.0.1",   # IPv4-mapped private
        ],
    )
    def test_blocks(self, ip):
        assert _is_blocked_ip(ip) is True

    @pytest.mark.parametrize(
        "ip",
        [
            "1.1.1.1",
            "8.8.8.8",
            "140.82.121.4",      # github.com (sample)
            "2606:4700:4700::1111",  # cloudflare DNS v6
        ],
    )
    def test_allows_public(self, ip):
        assert _is_blocked_ip(ip) is False

    def test_blocks_garbage(self):
        # Unparseable input is treated as blocked — fail closed.
        assert _is_blocked_ip("not-an-ip") is True


# ---------------------------------------------------------------------------
# _resolve_and_check — DNS layer
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestResolveAndCheck:
    def test_rejects_loopback_literal(self, monkeypatch):
        _patch_site_config(monkeypatch, {})
        with pytest.raises(SSRFBlockedError, match="127.0.0.1"):
            _resolve_and_check("http://127.0.0.1:9091/metrics")

    def test_rejects_ipv6_loopback_literal(self, monkeypatch):
        _patch_site_config(monkeypatch, {})
        with pytest.raises(SSRFBlockedError):
            _resolve_and_check("http://[::1]:8080/")

    def test_rejects_localhost_hostname(self, monkeypatch):
        _patch_site_config(monkeypatch, {})
        with pytest.raises(SSRFBlockedError, match="localhost"):
            _resolve_and_check("http://localhost:18443/")

    def test_rejects_rfc1918_via_dns(self, monkeypatch):
        _patch_site_config(monkeypatch, {})
        _mock_getaddrinfo(monkeypatch, {"intranet.example.com": ["10.0.0.5"]})
        with pytest.raises(SSRFBlockedError, match="10.0.0.5"):
            _resolve_and_check("http://intranet.example.com/")

    @pytest.mark.parametrize(
        "ip",
        ["10.5.5.5", "192.168.1.10", "172.16.0.1", "172.20.30.40"],
    )
    def test_rejects_private_ranges_via_dns(self, monkeypatch, ip):
        _patch_site_config(monkeypatch, {})
        _mock_getaddrinfo(monkeypatch, {"private.example.com": [ip]})
        with pytest.raises(SSRFBlockedError):
            _resolve_and_check("http://private.example.com/")

    def test_rejects_cloud_metadata_literal(self, monkeypatch):
        _patch_site_config(monkeypatch, {})
        with pytest.raises(SSRFBlockedError, match="169.254.169.254"):
            _resolve_and_check("http://169.254.169.254/latest/meta-data/")

    def test_rejects_cgnat_tailscale(self, monkeypatch):
        _patch_site_config(monkeypatch, {})
        with pytest.raises(SSRFBlockedError, match="100.64"):
            _resolve_and_check("http://100.64.1.50:3000/")

    def test_rejects_tailscale_via_dns(self, monkeypatch):
        _patch_site_config(monkeypatch, {})
        _mock_getaddrinfo(monkeypatch, {"node.tail.ts.net": ["100.64.0.42"]})
        with pytest.raises(SSRFBlockedError, match="100.64.0.42"):
            _resolve_and_check("https://node.tail.ts.net/")

    def test_allows_public_dns_result(self, monkeypatch):
        _patch_site_config(monkeypatch, {})
        _mock_getaddrinfo(monkeypatch, {"one.one.one.one": ["1.1.1.1"]})
        # Should NOT raise — public IP is allowed.
        _resolve_and_check("https://one.one.one.one/")

    def test_allows_8_8_8_8(self, monkeypatch):
        _patch_site_config(monkeypatch, {})
        _mock_getaddrinfo(monkeypatch, {"dns.google": ["8.8.8.8"]})
        _resolve_and_check("https://dns.google/resolve")

    def test_blocks_when_any_resolved_ip_is_private(self, monkeypatch):
        """Multi-A record where ONE entry is private should still reject —
        an attacker round-robins to the internal IP on connect."""
        _patch_site_config(monkeypatch, {})
        _mock_getaddrinfo(
            monkeypatch,
            {"mixed.example.com": ["1.1.1.1", "10.0.0.1"]},
        )
        with pytest.raises(SSRFBlockedError, match="10.0.0.1"):
            _resolve_and_check("https://mixed.example.com/")

    def test_dns_failure_raises_url_scrape_error(self, monkeypatch):
        _patch_site_config(monkeypatch, {})
        # No mapping → gaierror inside _resolve_hostname
        _mock_getaddrinfo(monkeypatch, {})
        with pytest.raises(URLScrapeError, match="DNS lookup failed"):
            _resolve_and_check("https://nope.invalid/")

    def test_override_flag_allows_loopback(self, monkeypatch):
        _patch_site_config(monkeypatch, {"url_scraper_allow_internal_ips": True})
        # No raise — operator override is in effect.
        _resolve_and_check("http://127.0.0.1:9091/")

    def test_override_flag_allows_private_via_dns(self, monkeypatch):
        _patch_site_config(monkeypatch, {"url_scraper_allow_internal_ips": "true"})
        _mock_getaddrinfo(monkeypatch, {"intranet.example.com": ["10.0.0.5"]})
        _resolve_and_check("http://intranet.example.com/")

    def test_no_hostname_raises(self, monkeypatch):
        _patch_site_config(monkeypatch, {})
        with pytest.raises(URLScrapeError, match="no hostname"):
            _resolve_and_check("http:///oops")


# ---------------------------------------------------------------------------
# _safe_get — redirect-aware fetch
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestSafeGetRedirects:
    async def test_blocks_redirect_to_loopback(self, monkeypatch):
        """A public page redirects to http://127.0.0.1 — must be rejected.

        This is the canonical SSRF-via-redirect scenario the guard exists for.
        """
        _patch_site_config(monkeypatch, {})
        _mock_getaddrinfo(monkeypatch, {"evil.example.com": ["1.2.3.4"]})

        # First hop: 302 → 127.0.0.1; second hop must be refused.
        client = _FakeAsyncClient([
            _FakeResponse(
                status_code=302,
                headers={"location": "http://127.0.0.1:9091/metrics"},
            ),
        ])

        with pytest.raises(SSRFBlockedError, match="127.0.0.1"):
            await _safe_get(client, "https://evil.example.com/")

    async def test_blocks_redirect_to_cloud_metadata(self, monkeypatch):
        _patch_site_config(monkeypatch, {})
        _mock_getaddrinfo(monkeypatch, {"public.example.com": ["1.2.3.4"]})
        client = _FakeAsyncClient([
            _FakeResponse(
                status_code=301,
                headers={"location": "http://169.254.169.254/latest/meta-data/"},
            ),
        ])
        with pytest.raises(SSRFBlockedError, match="169.254.169.254"):
            await _safe_get(client, "https://public.example.com/")

    async def test_blocks_redirect_via_dns_to_private(self, monkeypatch):
        _patch_site_config(monkeypatch, {})
        _mock_getaddrinfo(
            monkeypatch,
            {
                "public.example.com": ["1.2.3.4"],
                "evil-redirect.example.com": ["10.0.0.1"],
            },
        )
        client = _FakeAsyncClient([
            _FakeResponse(
                status_code=302,
                headers={"location": "https://evil-redirect.example.com/"},
            ),
        ])
        with pytest.raises(SSRFBlockedError, match="10.0.0.1"):
            await _safe_get(client, "https://public.example.com/")

    async def test_blocks_non_http_redirect_scheme(self, monkeypatch):
        _patch_site_config(monkeypatch, {})
        _mock_getaddrinfo(monkeypatch, {"public.example.com": ["1.2.3.4"]})
        client = _FakeAsyncClient([
            _FakeResponse(
                status_code=302,
                headers={"location": "file:///etc/passwd"},
            ),
        ])
        with pytest.raises(SSRFBlockedError, match="non-http"):
            await _safe_get(client, "https://public.example.com/")

    async def test_allows_redirect_to_public_ip(self, monkeypatch):
        _patch_site_config(monkeypatch, {})
        _mock_getaddrinfo(
            monkeypatch,
            {
                "public.example.com": ["1.2.3.4"],
                "elsewhere.example.com": ["8.8.8.8"],
            },
        )
        client = _FakeAsyncClient([
            _FakeResponse(
                status_code=302,
                headers={"location": "https://elsewhere.example.com/landing"},
            ),
            _FakeResponse(status_code=200, text="hello"),
        ])
        resp = await _safe_get(client, "https://public.example.com/")
        assert resp.status_code == 200
        assert resp.text == "hello"
        assert client.requested_urls == [
            "https://public.example.com/",
            "https://elsewhere.example.com/landing",
        ]

    async def test_caps_redirect_chain(self, monkeypatch):
        _patch_site_config(monkeypatch, {})
        _mock_getaddrinfo(monkeypatch, {"public.example.com": ["1.2.3.4"]})
        # Endless 302 loop back to itself; should give up after MAX_REDIRECTS.
        responses = [
            _FakeResponse(
                status_code=302,
                headers={"location": "https://public.example.com/loop"},
            )
            for _ in range(MAX_REDIRECTS + 2)
        ]
        client = _FakeAsyncClient(responses)
        with pytest.raises(URLScrapeError, match="Too many redirects"):
            await _safe_get(client, "https://public.example.com/")


# ---------------------------------------------------------------------------
# scrape_url end-to-end — the public surface the route handler hits
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestScrapeUrlSSRF:
    async def test_blocks_127_0_0_1_at_entry(self, monkeypatch):
        _patch_site_config(monkeypatch, {})
        with pytest.raises(SSRFBlockedError):
            await url_scraper.scrape_url("http://127.0.0.1:9091/metrics")

    async def test_blocks_localhost_at_entry(self, monkeypatch):
        _patch_site_config(monkeypatch, {})
        with pytest.raises(SSRFBlockedError):
            await url_scraper.scrape_url("http://localhost:18443/")

    async def test_blocks_cloud_metadata_at_entry(self, monkeypatch):
        _patch_site_config(monkeypatch, {})
        with pytest.raises(SSRFBlockedError):
            await url_scraper.scrape_url("http://169.254.169.254/")

    async def test_blocks_tailscale_cgnat_at_entry(self, monkeypatch):
        _patch_site_config(monkeypatch, {})
        with pytest.raises(SSRFBlockedError):
            await url_scraper.scrape_url("http://100.64.0.42:3000/")

    async def test_override_flag_unblocks_loopback_end_to_end(self, monkeypatch):
        """With url_scraper_allow_internal_ips=true the call proceeds.

        We don't care that the eventual HTTP hits an internal box — we only
        care that the SSRF gate stopped raising. Mock httpx so no real
        network call escapes the test.
        """
        _patch_site_config(
            monkeypatch, {"url_scraper_allow_internal_ips": True},
        )
        # Fake the AsyncClient so scrape_url -> _fetch -> _safe_get sees
        # a tame 200 response.
        client = _FakeAsyncClient([
            _FakeResponse(
                status_code=200,
                text="<html><head><title>internal</title></head><body><article><p>x</p></article></body></html>",
            ),
        ])
        monkeypatch.setattr(
            url_scraper.httpx,
            "AsyncClient",
            lambda **kwargs: client,
        )
        out = await url_scraper.scrape_url("http://127.0.0.1:9091/metrics")
        assert isinstance(out, dict)
        # Title parsed without the SSRF gate firing → contract met.
        assert "internal" in out["title"].lower()
