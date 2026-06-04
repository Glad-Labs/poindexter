"""Unit tests for ``utils/edge_challenge.py``."""

from __future__ import annotations

from utils.edge_challenge import is_edge_challenge


class _Resp:
    def __init__(self, status_code, headers=None):
        self.status_code = status_code
        self.headers = headers or {}


class TestIsEdgeChallenge:
    def test_403_with_cf_mitigated_is_challenge(self):
        assert is_edge_challenge(_Resp(403, {"cf-mitigated": "challenge"})) is True

    def test_503_with_cf_mitigated_is_challenge(self):
        assert is_edge_challenge(_Resp(503, {"cf-mitigated": "challenge"})) is True

    def test_429_with_cf_mitigated_is_challenge(self):
        assert is_edge_challenge(_Resp(429, {"cf-mitigated": "challenge"})) is True

    def test_403_without_cf_header_is_not_challenge(self):
        # Genuine origin 403 proxied through Cloudflare — still a real failure.
        assert is_edge_challenge(_Resp(403, {"server": "cloudflare"})) is False

    def test_200_is_never_a_challenge(self):
        assert is_edge_challenge(_Resp(200, {"cf-mitigated": "challenge"})) is False

    def test_404_is_not_a_challenge(self):
        assert is_edge_challenge(_Resp(404, {"cf-mitigated": "challenge"})) is False

    def test_missing_headers_attr_is_safe(self):
        class _Bare:
            status_code = 403
        assert is_edge_challenge(_Bare()) is False
