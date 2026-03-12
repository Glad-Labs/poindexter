"""
Unit tests for webhook_security.py.

Covers:
  - WebhookSecurity.verify_signature: valid sig passes, tampered payload raises
  - WebhookSecurity.calculate_signature: deterministic across calls
  - WebhookRateLimiter.is_allowed: sliding window allows/denies correctly
  - WebhookValidator.validate_payload_size: max size enforcement
  - WebhookValidator.validate_content_type: allowlist check

All tests are pure in-process — no DB, no HTTP.
"""

import hashlib
import hmac
from datetime import datetime, timedelta, timezone

import pytest

from services.webhook_security import (
    WebhookRateLimiter,
    WebhookSecurity,
    WebhookSignatureError,
    WebhookValidator,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_SECRET = "webhook-unit-test-secret"
_PAYLOAD = b'{"event": "test", "data": {"id": 42}}'


def _sign(payload: bytes, secret: str, timestamp: str | None = None) -> str:
    """Compute the HMAC-SHA256 signature the same way WebhookSecurity does."""
    if timestamp:
        content = f"{timestamp}.".encode() + payload
    else:
        content = payload
    return hmac.new(secret.encode(), content, hashlib.sha256).hexdigest()


# ---------------------------------------------------------------------------
# WebhookSecurity.verify_signature
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestVerifySignature:
    def test_valid_signature_without_timestamp_returns_true(self):
        sig = _sign(_PAYLOAD, _SECRET)
        result = WebhookSecurity.verify_signature(
            _PAYLOAD, sig, _SECRET, check_timestamp=False
        )
        assert result is True

    def test_valid_signature_with_recent_timestamp_returns_true(self):
        ts = datetime.now(timezone.utc).isoformat()
        sig = _sign(_PAYLOAD, _SECRET, ts)
        result = WebhookSecurity.verify_signature(
            _PAYLOAD, sig, _SECRET, timestamp=ts, check_timestamp=True
        )
        assert result is True

    def test_tampered_payload_raises(self):
        sig = _sign(_PAYLOAD, _SECRET)
        tampered = _PAYLOAD + b"extra"
        with pytest.raises(WebhookSignatureError):
            WebhookSecurity.verify_signature(
                tampered, sig, _SECRET, check_timestamp=False
            )

    def test_wrong_secret_raises(self):
        sig = _sign(_PAYLOAD, "wrong-secret")
        with pytest.raises(WebhookSignatureError):
            WebhookSecurity.verify_signature(
                _PAYLOAD, sig, _SECRET, check_timestamp=False
            )

    def test_missing_signature_raises(self):
        with pytest.raises(WebhookSignatureError):
            WebhookSecurity.verify_signature(
                _PAYLOAD, "", _SECRET, check_timestamp=False
            )

    def test_signature_wrong_length_raises(self):
        with pytest.raises(WebhookSignatureError):
            WebhookSecurity.verify_signature(
                _PAYLOAD, "tooshort", _SECRET, check_timestamp=False
            )

    def test_expired_timestamp_raises(self):
        old_ts = (datetime.now(timezone.utc) - timedelta(minutes=10)).isoformat()
        sig = _sign(_PAYLOAD, _SECRET, old_ts)
        with pytest.raises(WebhookSignatureError, match="too old"):
            WebhookSecurity.verify_signature(
                _PAYLOAD, sig, _SECRET, timestamp=old_ts, check_timestamp=True
            )

    def test_invalid_timestamp_format_raises(self):
        sig = _sign(_PAYLOAD, _SECRET, "not-a-date")
        with pytest.raises(WebhookSignatureError):
            WebhookSecurity.verify_signature(
                _PAYLOAD, sig, _SECRET, timestamp="not-a-date", check_timestamp=True
            )


# ---------------------------------------------------------------------------
# WebhookSecurity.calculate_signature — determinism
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestCalculateSignature:
    def test_same_inputs_produce_same_signature(self):
        sig1 = WebhookSecurity.calculate_signature(_PAYLOAD, _SECRET)
        sig2 = WebhookSecurity.calculate_signature(_PAYLOAD, _SECRET)
        assert sig1 == sig2

    def test_different_payloads_produce_different_signatures(self):
        sig1 = WebhookSecurity.calculate_signature(b"payload-A", _SECRET)
        sig2 = WebhookSecurity.calculate_signature(b"payload-B", _SECRET)
        assert sig1 != sig2

    def test_different_secrets_produce_different_signatures(self):
        sig1 = WebhookSecurity.calculate_signature(_PAYLOAD, "secret-A")
        sig2 = WebhookSecurity.calculate_signature(_PAYLOAD, "secret-B")
        assert sig1 != sig2

    def test_signature_is_64_char_hex(self):
        sig = WebhookSecurity.calculate_signature(_PAYLOAD, _SECRET)
        assert len(sig) == 64
        int(sig, 16)  # Will raise ValueError if not valid hex

    def test_with_timestamp_differs_from_without(self):
        ts = datetime.now(timezone.utc).isoformat()
        with_ts = WebhookSecurity.calculate_signature(_PAYLOAD, _SECRET, timestamp=ts)
        without_ts = WebhookSecurity.calculate_signature(_PAYLOAD, _SECRET)
        assert with_ts != without_ts


# ---------------------------------------------------------------------------
# WebhookRateLimiter.is_allowed
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestWebhookRateLimiter:
    def test_first_request_allowed(self):
        limiter = WebhookRateLimiter(max_requests_per_minute=5)
        assert limiter.is_allowed("source-1") is True

    def test_requests_within_limit_all_allowed(self):
        limiter = WebhookRateLimiter(max_requests_per_minute=3)
        for _ in range(3):
            assert limiter.is_allowed("source-1") is True

    def test_request_beyond_limit_denied(self):
        limiter = WebhookRateLimiter(max_requests_per_minute=3)
        for _ in range(3):
            limiter.is_allowed("source-1")
        assert limiter.is_allowed("source-1") is False

    def test_different_sources_have_independent_limits(self):
        limiter = WebhookRateLimiter(max_requests_per_minute=1)
        limiter.is_allowed("source-A")  # Exhaust limit for A
        assert limiter.is_allowed("source-B") is True  # B is unaffected

    def test_old_requests_evicted_from_window(self):
        """Artificially inject an old timestamp so the window evicts it."""
        limiter = WebhookRateLimiter(max_requests_per_minute=1)
        # Directly inject a timestamp that is > 1 minute old
        old_ts = datetime.now() - timedelta(minutes=2)
        limiter.requests["source-1"] = [old_ts]
        # The old entry should be evicted; new request should be allowed
        assert limiter.is_allowed("source-1") is True


# ---------------------------------------------------------------------------
# WebhookValidator.validate_payload_size
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestValidatePayloadSize:
    def test_small_payload_passes(self):
        result = WebhookValidator.validate_payload_size(b"x" * 100, max_size_mb=1)
        assert result is True

    def test_exactly_max_size_passes(self):
        max_bytes = 1 * 1024 * 1024  # 1 MB
        result = WebhookValidator.validate_payload_size(b"x" * max_bytes, max_size_mb=1)
        assert result is True

    def test_payload_exceeding_max_raises(self):
        max_bytes = 1 * 1024 * 1024  # 1 MB
        with pytest.raises(WebhookSignatureError, match="exceeds maximum"):
            WebhookValidator.validate_payload_size(b"x" * (max_bytes + 1), max_size_mb=1)

    def test_empty_payload_passes(self):
        result = WebhookValidator.validate_payload_size(b"", max_size_mb=1)
        assert result is True


# ---------------------------------------------------------------------------
# WebhookValidator.validate_content_type
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestValidateContentType:
    def test_application_json_allowed(self):
        result = WebhookValidator.validate_content_type("application/json")
        assert result is True

    def test_form_urlencoded_allowed(self):
        result = WebhookValidator.validate_content_type("application/x-www-form-urlencoded")
        assert result is True

    def test_json_with_charset_allowed(self):
        """Content-Type with charset parameter should still be accepted."""
        result = WebhookValidator.validate_content_type(
            "application/json; charset=utf-8"
        )
        assert result is True

    def test_text_plain_raises(self):
        with pytest.raises(WebhookSignatureError, match="Invalid content type"):
            WebhookValidator.validate_content_type("text/plain")

    def test_custom_allowlist_respected(self):
        result = WebhookValidator.validate_content_type(
            "text/xml", allowed_types=["text/xml"]
        )
        assert result is True

    def test_type_not_in_custom_allowlist_raises(self):
        with pytest.raises(WebhookSignatureError):
            WebhookValidator.validate_content_type(
                "application/json", allowed_types=["text/xml"]
            )
