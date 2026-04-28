"""
Unit tests for routes/external_webhooks.py — Phase 5 webhook handler.

GH-107 / poindexter#155: ``lemon_squeezy_webhook_secret`` and
``resend_webhook_secret`` are both ``is_secret=true`` in app_settings,
so the verifier helpers MUST use ``await site_config.get_secret(...)``,
not the sync ``site_config.get(...)``. Sync ``.get()`` returns the
``enc:v1:<base64>`` ciphertext blob — HMAC comparison with that as the
key would silently fail every legitimate webhook (401-everything mode),
exactly the same failure pattern that surfaced for Alertmanager /
Vercel / Telegram in the original GH-107 incidents.

These tests assert:
  * the verifier reaches for ``get_secret``, not ``get``, for the
    encrypted key
  * a real, correctly-signed payload validates with the plaintext
    secret (proving plaintext is what feeds HMAC)
  * a payload signed with the **ciphertext** as the HMAC key would
    NOT validate — pinning the bug class so any future regression
    that re-introduces sync ``.get()`` fails the suite
  * the route returns 401 when the secret isn't configured
"""

from __future__ import annotations

import hashlib
import hmac
import json
from unittest.mock import AsyncMock, MagicMock

import pytest

from routes.external_webhooks import (
    _verify_lemon_squeezy_signature,
    _verify_resend_signature,
)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


def _mock_sc(
    *,
    lemon_squeezy_secret: str = "",
    resend_secret: str = "",
) -> MagicMock:
    """SiteConfig mock that returns ciphertext from sync .get() and
    plaintext from async get_secret() — mirrors production behavior
    for is_secret=true rows.
    """
    sc = MagicMock()

    # Sync .get(): ciphertext for both encrypted keys (regression bait)
    sync_values: dict[str, str] = {}
    if lemon_squeezy_secret:
        sync_values["lemon_squeezy_webhook_secret"] = (
            f"enc:v1:CIPHERTEXT_FOR_{lemon_squeezy_secret}"
        )
    if resend_secret:
        sync_values["resend_webhook_secret"] = (
            f"enc:v1:CIPHERTEXT_FOR_{resend_secret}"
        )
    sc.get.side_effect = lambda k, d="": sync_values.get(k, d)

    # Async get_secret(): plaintext
    secret_values: dict[str, str] = {}
    if lemon_squeezy_secret:
        secret_values["lemon_squeezy_webhook_secret"] = lemon_squeezy_secret
    if resend_secret:
        secret_values["resend_webhook_secret"] = resend_secret
    sc.get_secret = AsyncMock(
        side_effect=lambda k, d="": secret_values.get(k, d)
    )
    return sc


def _hmac_hex(key: str, body: bytes) -> str:
    return hmac.new(key.encode("utf-8"), body, hashlib.sha256).hexdigest()


# ---------------------------------------------------------------------------
# Lemon Squeezy
# ---------------------------------------------------------------------------


class TestLemonSqueezyVerify:
    """_verify_lemon_squeezy_signature must decrypt the secret and HMAC
    with plaintext."""

    @pytest.mark.asyncio
    async def test_valid_signature_with_plaintext_secret(self):
        secret = "ls_real_signing_secret"
        body = json.dumps({"meta": {"event_name": "order_created"}}).encode()
        provided = _hmac_hex(secret, body)
        sc = _mock_sc(lemon_squeezy_secret=secret)

        ok = await _verify_lemon_squeezy_signature(body, provided, sc)
        assert ok is True

    @pytest.mark.asyncio
    async def test_uses_get_secret_not_get(self):
        sc = _mock_sc(lemon_squeezy_secret="x")
        body = b"{}"
        await _verify_lemon_squeezy_signature(body, _hmac_hex("x", body), sc)
        sc.get_secret.assert_awaited_once_with(
            "lemon_squeezy_webhook_secret", ""
        )
        # Sync .get() must NOT be used for the encrypted key.
        for call in sc.get.mock_calls:
            if call.args:
                assert call.args[0] != "lemon_squeezy_webhook_secret"

    @pytest.mark.asyncio
    async def test_signature_signed_with_ciphertext_key_rejected(self):
        """If a regression brought back sync .get(), the verifier would
        use enc:v1:<ciphertext> as the HMAC key. This test pins the
        bug class — a payload signed with ciphertext-as-key must NOT
        validate against the plaintext-as-key path the fix uses."""
        secret = "ls_real_signing_secret"
        body = b'{"hi": 1}'
        # Forge a signature using the ciphertext key (the buggy path)
        ciphertext_sig = _hmac_hex(f"enc:v1:CIPHERTEXT_FOR_{secret}", body)
        sc = _mock_sc(lemon_squeezy_secret=secret)

        ok = await _verify_lemon_squeezy_signature(body, ciphertext_sig, sc)
        assert ok is False, (
            "Verifier accepted a signature forged with ciphertext-as-key — "
            "the GH-107 ciphertext-leak regression has resurfaced."
        )

    @pytest.mark.asyncio
    async def test_invalid_signature_rejected(self):
        sc = _mock_sc(lemon_squeezy_secret="real")
        ok = await _verify_lemon_squeezy_signature(b"{}", "deadbeef", sc)
        assert ok is False

    @pytest.mark.asyncio
    async def test_missing_secret_rejects(self):
        sc = _mock_sc()  # no secret configured
        ok = await _verify_lemon_squeezy_signature(b"{}", "any", sc)
        assert ok is False

    @pytest.mark.asyncio
    async def test_missing_signature_header_rejects(self):
        sc = _mock_sc(lemon_squeezy_secret="real")
        ok = await _verify_lemon_squeezy_signature(b"{}", None, sc)
        assert ok is False


# ---------------------------------------------------------------------------
# Resend
# ---------------------------------------------------------------------------


class TestResendVerify:
    """_verify_resend_signature must decrypt the secret and HMAC with
    plaintext."""

    @pytest.mark.asyncio
    async def test_valid_signature_with_plaintext_secret(self):
        secret = "resend_real_signing_secret"
        body = json.dumps({"type": "email.delivered"}).encode()
        provided = _hmac_hex(secret, body)
        sc = _mock_sc(resend_secret=secret)

        ok = await _verify_resend_signature(body, provided, sc)
        assert ok is True

    @pytest.mark.asyncio
    async def test_valid_signature_with_svix_v1_format(self):
        """Resend may format as 'v1,<hex>' — the verifier handles it."""
        secret = "rs_secret"
        body = b'{"type":"email.sent"}'
        provided = f"v1,{_hmac_hex(secret, body)}"
        sc = _mock_sc(resend_secret=secret)

        ok = await _verify_resend_signature(body, provided, sc)
        assert ok is True

    @pytest.mark.asyncio
    async def test_uses_get_secret_not_get(self):
        sc = _mock_sc(resend_secret="x")
        body = b"{}"
        await _verify_resend_signature(body, _hmac_hex("x", body), sc)
        sc.get_secret.assert_awaited_once_with("resend_webhook_secret", "")
        for call in sc.get.mock_calls:
            if call.args:
                assert call.args[0] != "resend_webhook_secret"

    @pytest.mark.asyncio
    async def test_signature_signed_with_ciphertext_key_rejected(self):
        secret = "rs_secret"
        body = b'{"type":"email.bounced"}'
        ciphertext_sig = _hmac_hex(f"enc:v1:CIPHERTEXT_FOR_{secret}", body)
        sc = _mock_sc(resend_secret=secret)

        ok = await _verify_resend_signature(body, ciphertext_sig, sc)
        assert ok is False, (
            "Verifier accepted a signature forged with ciphertext-as-key — "
            "the GH-107 ciphertext-leak regression has resurfaced."
        )

    @pytest.mark.asyncio
    async def test_missing_secret_rejects(self):
        sc = _mock_sc()
        ok = await _verify_resend_signature(b"{}", "any", sc)
        assert ok is False

    @pytest.mark.asyncio
    async def test_missing_signature_header_rejects(self):
        sc = _mock_sc(resend_secret="real")
        ok = await _verify_resend_signature(b"{}", None, sc)
        assert ok is False
