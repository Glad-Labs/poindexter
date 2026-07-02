"""Unit tests for ``poindexter.cli._token_cache`` (cross-process JWT cache).

The CLI runs as a fresh process on every invocation, so ``OAuthClient``'s
in-memory token cache never survives — every ``poindexter <cmd>`` re-reads
the OAuth client from the app_settings DB (host->5433) and re-mints a JWT
(host->8002). On Windows + Docker Desktop both hops traverse the flaky
host port-proxy, which is the source of the recurring "No CLI OAuth
credentials" / ``WinError 64`` wedges.

This module persists the minted JWT to ``~/.poindexter/`` so a still-fresh
token is reused across invocations, skipping *both* the DB read and the
mint on the hot path. These tests pin the contract:

* round-trips a token keyed by base_url
* refuses stale / near-expiry / undecodable / corrupt entries (fail to a
  cache miss, never hand out a bad token, never raise)
* isolates base_urls from each other
* honours the ``POINDEXTER_CLI_TOKEN_CACHE`` kill-switch
* degrades to a no-op (never raises) when the cache dir is unwritable
* writes 0600 on POSIX
"""

from __future__ import annotations

import base64
import json
import os
import time

import pytest

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_jwt(exp_offset: int = 3600) -> str:
    """A JWT-shaped string whose ``exp`` is ``exp_offset`` seconds out.

    Signature is never verified client-side; the cache only base64-decodes
    the payload to read ``exp`` (via ``oauth_client._decode_jwt_exp``).
    """
    header = {"alg": "HS256", "typ": "JWT"}
    payload = {
        "iss": "poindexter",
        "sub": "pdx_cli",
        "exp": int(time.time()) + exp_offset,
        "jti": "cache-test",
    }

    def _b64(d: dict) -> str:
        raw = json.dumps(d, separators=(",", ":")).encode()
        return base64.urlsafe_b64encode(raw).rstrip(b"=").decode("ascii")

    return f"{_b64(header)}.{_b64(payload)}.signature"


_URL = "http://localhost:8002"
# A distinct second base_url for isolation tests. RFC5737 TEST-NET-2
# (documentation range) — a placeholder, never a real operator address.
_URL2 = "http://198.51.100.10:8002"

# The cache dir is isolated to a per-test tmp_path by the autouse
# ``_isolate_cli_token_cache`` fixture in ``tests/unit/cli/conftest.py``.


# ---------------------------------------------------------------------------
# Round-trip
# ---------------------------------------------------------------------------


class TestRoundTrip:
    def test_save_then_load_returns_token(self):
        from poindexter.cli._token_cache import load_token, save_token

        token = _make_jwt(exp_offset=3600)
        save_token(_URL, token)
        assert load_token(_URL) == token

    def test_load_missing_returns_none(self):
        from poindexter.cli._token_cache import load_token

        assert load_token(_URL) is None

    def test_save_overwrites_previous_token_for_same_url(self):
        from poindexter.cli._token_cache import load_token, save_token

        first = _make_jwt(exp_offset=3600)
        second = _make_jwt(exp_offset=1800)
        save_token(_URL, first)
        save_token(_URL, second)
        assert load_token(_URL) == second

    def test_base_urls_are_isolated(self):
        from poindexter.cli._token_cache import load_token, save_token

        a = _make_jwt(exp_offset=3600)
        b = _make_jwt(exp_offset=3600)
        save_token(_URL, a)
        save_token(_URL2, b)
        assert load_token(_URL) == a
        assert load_token(_URL2) == b

    def test_trailing_slash_in_url_is_normalised(self):
        from poindexter.cli._token_cache import load_token, save_token

        token = _make_jwt(exp_offset=3600)
        save_token(_URL + "/", token)
        assert load_token(_URL) == token


# ---------------------------------------------------------------------------
# Freshness — never hand out a bad token
# ---------------------------------------------------------------------------


class TestFreshness:
    def test_expired_token_returns_none(self):
        from poindexter.cli._token_cache import load_token, save_token

        save_token(_URL, _make_jwt(exp_offset=-10))
        assert load_token(_URL) is None

    def test_within_skew_window_returns_none(self):
        """A token that expires in <30 s (the mint-time skew) is treated
        as already stale, matching ``OAuthClient``'s refresh window."""
        from poindexter.cli._token_cache import load_token, save_token

        save_token(_URL, _make_jwt(exp_offset=5))
        assert load_token(_URL) is None

    def test_undecodable_token_returns_none(self):
        """A persisted value we can't read an ``exp`` from can't be proven
        fresh — refuse it and force a re-mint rather than gamble."""
        from poindexter.cli._token_cache import load_token, save_token

        save_token(_URL, "not-a-jwt")
        assert load_token(_URL) is None


# ---------------------------------------------------------------------------
# Robustness — corruption / unwritable dir must never raise or wedge a command
# ---------------------------------------------------------------------------


class TestRobustness:
    def test_corrupt_json_returns_none(self):
        from poindexter.cli._token_cache import _cache_path, load_token

        _cache_path().write_text("{ this is not json", encoding="utf-8")
        assert load_token(_URL) is None

    def test_wrong_shape_returns_none(self):
        from poindexter.cli._token_cache import _cache_path, load_token

        _cache_path().write_text(json.dumps({"unexpected": "shape"}), encoding="utf-8")
        assert load_token(_URL) is None

    def test_save_never_raises_when_dir_unwritable(self, tmp_path, monkeypatch):
        from poindexter.cli._token_cache import load_token, save_token

        # Make the cache "dir" sit under a regular file so mkdir fails.
        blocker = tmp_path / "blocker"
        blocker.write_text("x", encoding="utf-8")
        monkeypatch.setenv("POINDEXTER_TOKEN_CACHE_DIR", str(blocker / "cache"))

        save_token(_URL, _make_jwt())  # must not raise
        assert load_token(_URL) is None  # must not raise

    def test_load_never_raises_on_garbage_and_returns_none(self):
        from poindexter.cli._token_cache import _cache_path, load_token

        _cache_path().write_bytes(b"\x00\x01\x02\xff")
        assert load_token(_URL) is None


# ---------------------------------------------------------------------------
# Clear
# ---------------------------------------------------------------------------


class TestClear:
    def test_clear_removes_only_that_url(self):
        from poindexter.cli._token_cache import clear_token, load_token, save_token

        save_token(_URL, _make_jwt())
        save_token(_URL2, _make_jwt())
        clear_token(_URL)
        assert load_token(_URL) is None
        assert load_token(_URL2) is not None

    def test_clear_missing_is_noop(self):
        from poindexter.cli._token_cache import clear_token

        clear_token(_URL)  # must not raise


# ---------------------------------------------------------------------------
# Kill-switch
# ---------------------------------------------------------------------------


class TestKillSwitch:
    @pytest.mark.parametrize("value", ["0", "false", "no", "off", "FALSE"])
    def test_disabled_makes_save_noop_and_load_none(self, monkeypatch, value):
        from poindexter.cli._token_cache import load_token, save_token

        monkeypatch.setenv("POINDEXTER_CLI_TOKEN_CACHE", value)
        save_token(_URL, _make_jwt())
        assert load_token(_URL) is None

    def test_enabled_by_default(self):
        from poindexter.cli._token_cache import load_token, save_token

        save_token(_URL, _make_jwt())
        assert load_token(_URL) is not None


# ---------------------------------------------------------------------------
# On-disk hygiene
# ---------------------------------------------------------------------------


class TestOnDiskHygiene:
    @pytest.mark.skipif(os.name == "nt", reason="POSIX file mode semantics")
    def test_saved_file_is_owner_only(self):
        from poindexter.cli._token_cache import _cache_path, save_token

        save_token(_URL, _make_jwt())
        mode = _cache_path().stat().st_mode & 0o777
        assert mode == 0o600, f"expected 0600, got {oct(mode)}"

    def test_cache_file_lives_in_configured_dir(self, tmp_path):
        from poindexter.cli._token_cache import _cache_path, save_token

        save_token(_URL, _make_jwt())
        assert _cache_path().parent == tmp_path
        assert _cache_path().exists()


# ---------------------------------------------------------------------------
# CliTokenStore adapter — the async seam OAuthClient consumes
# ---------------------------------------------------------------------------


class TestCliTokenStore:
    @pytest.mark.asyncio
    async def test_store_roundtrips(self):
        from poindexter.cli._token_cache import CliTokenStore

        store = CliTokenStore(_URL)
        token = _make_jwt()
        await store.save(token)
        assert await store.load() == token
        await store.clear()
        assert await store.load() is None
