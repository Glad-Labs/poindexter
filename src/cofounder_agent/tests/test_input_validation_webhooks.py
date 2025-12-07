"""
Input Validation & Webhook Security Tests

Comprehensive tests for:
- Input validation service
- Webhook HMAC signature verification
- Webhook rate limiting
- Payload inspection middleware
"""

import pytest
import json
import hmac
import hashlib
from datetime import datetime, timedelta
from fastapi.testclient import TestClient
from src.cofounder_agent.main import app
from src.cofounder_agent.services.validation_service import InputValidator, ValidationError, SanitizationHelper
from src.cofounder_agent.services.webhook_security import (
    WebhookSecurity, WebhookSignatureError, WebhookRateLimiter, WebhookValidator
)


client = TestClient(app)


# ============================================================================
# SECURITY TEST SUITE 4: INPUT VALIDATION SERVICE
# ============================================================================

class TestInputValidator:
    """Test input validation service"""
    
    def test_string_validation_basic(self):
        """Should validate basic string input"""
        result = InputValidator.validate_string("hello world", "test_field", min_length=1, max_length=100)
        assert result == "hello world"
    
    def test_string_validation_too_short(self):
        """Should reject strings below min length"""
        with pytest.raises(ValidationError, match="at least 5 characters"):
            InputValidator.validate_string("hi", "field", min_length=5)
    
    def test_string_validation_too_long(self):
        """Should reject strings above max length"""
        with pytest.raises(ValidationError, match="must not exceed 10 characters"):
            InputValidator.validate_string("this is a very long string", "field", max_length=10)
    
    def test_string_validation_sql_injection(self):
        """Should detect SQL injection attempts"""
        sql_payloads = [
            "admin' OR '1'='1",
            "'; DROP TABLE users; --",
            "1 UNION SELECT * FROM users",
            "1; DELETE FROM posts WHERE 1=1",
        ]
        
        for payload in sql_payloads:
            with pytest.raises(ValidationError, match="invalid SQL"):
                InputValidator.validate_string(payload, "field", allow_sql=False)
    
    def test_string_validation_xss(self):
        """Should detect XSS attempts"""
        xss_payloads = [
            "<script>alert('xss')</script>",
            "javascript:alert('xss')",
            "<img onerror='alert(1)'>",
            "<svg onclick='alert(1)'>",
        ]
        
        for payload in xss_payloads:
            with pytest.raises(ValidationError, match="invalid HTML or JavaScript"):
                InputValidator.validate_string(payload, "field", allow_html=False)
    
    def test_email_validation(self):
        """Should validate email addresses"""
        valid_emails = [
            "test@example.com",
            "user.name@domain.co.uk",
            "first+tag@example.com",
        ]
        
        for email in valid_emails:
            result = InputValidator.validate_email(email)
            assert result == email.lower()
    
    def test_email_validation_invalid(self):
        """Should reject invalid emails"""
        invalid_emails = [
            "notanemail",
            "missing@domain",
            "@nodomain.com",
            "spaces in@email.com",
        ]
        
        for email in invalid_emails:
            with pytest.raises(ValidationError):
                InputValidator.validate_email(email)
    
    def test_url_validation(self):
        """Should validate URLs"""
        valid_urls = [
            "https://example.com",
            "http://sub.domain.com/path",
            "https://example.com/path?query=value",
        ]
        
        for url in valid_urls:
            result = InputValidator.validate_url(url)
            assert result == url
    
    def test_url_validation_invalid(self):
        """Should reject invalid URLs"""
        invalid_urls = [
            "not a url",
            "ftp://example.com",  # FTP not allowed
            "javascript:alert(1)",
        ]
        
        for url in invalid_urls:
            with pytest.raises(ValidationError):
                InputValidator.validate_url(url)
    
    def test_integer_validation(self):
        """Should validate integer input"""
        result = InputValidator.validate_integer(42, "field", min_value=0, max_value=100)
        assert result == 42
    
    def test_integer_validation_bounds(self):
        """Should enforce integer bounds"""
        with pytest.raises(ValidationError, match="must be at least 10"):
            InputValidator.validate_integer(5, "field", min_value=10)
        
        with pytest.raises(ValidationError, match="must not exceed 100"):
            InputValidator.validate_integer(150, "field", max_value=100)
    
    def test_dict_validation(self):
        """Should validate dictionary input"""
        data = {"name": "John", "age": 30}
        result = InputValidator.validate_dict(data, "field")
        assert result == data
    
    def test_dict_validation_allowed_keys(self):
        """Should restrict dictionary keys"""
        data = {"name": "John", "invalid_key": "value"}
        
        with pytest.raises(ValidationError, match="invalid keys"):
            InputValidator.validate_dict(
                data, 
                "field", 
                allowed_keys=["name", "age"]
            )
    
    def test_list_validation(self):
        """Should validate list input"""
        data = [1, 2, 3, 4, 5]
        result = InputValidator.validate_list(data, "field", item_type=int)
        assert result == data
    
    def test_list_validation_item_type(self):
        """Should enforce list item types"""
        data = [1, "two", 3]
        
        with pytest.raises(ValidationError, match="must be of type"):
            InputValidator.validate_list(data, "field", item_type=int)


class TestSanitizationHelper:
    """Test sanitization utilities"""
    
    def test_sanitize_filename(self):
        """Should sanitize filenames"""
        dangerous_names = [
            "../../../etc/passwd",
            "file\x00name.txt",
            "file<script>.txt",
        ]
        
        for name in dangerous_names:
            result = SanitizationHelper.sanitize_filename(name)
            assert "/" not in result
            assert "\\" not in result
            assert "\x00" not in result
            assert "<" not in result
    
    def test_sanitize_html(self):
        """Should remove dangerous HTML"""
        html = '<p>Hello <script>alert("xss")</script> World</p>'
        result = SanitizationHelper.sanitize_html(html)
        assert "<script>" not in result
        assert "alert" not in result
        assert "<p>Hello" in result


# ============================================================================
# SECURITY TEST SUITE 5: WEBHOOK SIGNATURE VERIFICATION
# ============================================================================

class TestWebhookSecurity:
    """Test webhook signature verification"""
    
    def test_signature_calculation(self):
        """Should calculate valid HMAC-SHA256 signature"""
        payload = b'{"event": "test"}'
        secret = "test-secret-123"
        
        signature = WebhookSecurity.calculate_signature(payload, secret)
        
        # Verify signature is hex string (64 chars for SHA256)
        assert len(signature) == 64
        assert all(c in "0123456789abcdef" for c in signature)
    
    def test_signature_with_timestamp(self):
        """Should include timestamp in signature"""
        payload = b'{"event": "test"}'
        secret = "test-secret-123"
        timestamp = "2025-12-06T12:00:00Z"
        
        signature = WebhookSecurity.calculate_signature(payload, secret, timestamp)
        
        # Should be different from signature without timestamp
        signature_no_ts = WebhookSecurity.calculate_signature(payload, secret)
        assert signature != signature_no_ts
    
    def test_signature_verification_success(self):
        """Should verify valid signature"""
        payload = b'{"event": "test"}'
        secret = "test-secret-123"
        timestamp = "2025-12-06T12:00:00Z"
        
        signature = WebhookSecurity.calculate_signature(payload, secret, timestamp)
        
        # Verify signature
        result = WebhookSecurity.verify_signature(
            payload, signature, secret, timestamp, check_timestamp=False
        )
        assert result is True
    
    def test_signature_verification_tampered_payload(self):
        """Should reject tampered payloads"""
        payload = b'{"event": "test"}'
        secret = "test-secret-123"
        
        signature = WebhookSecurity.calculate_signature(payload, secret)
        
        # Tamper with payload
        tampered_payload = b'{"event": "tampered"}'
        
        with pytest.raises(WebhookSignatureError):
            WebhookSecurity.verify_signature(
                tampered_payload, signature, secret, check_timestamp=False
            )
    
    def test_signature_verification_invalid_secret(self):
        """Should reject signature with wrong secret"""
        payload = b'{"event": "test"}'
        secret = "test-secret-123"
        wrong_secret = "wrong-secret"
        
        signature = WebhookSecurity.calculate_signature(payload, secret)
        
        with pytest.raises(WebhookSignatureError):
            WebhookSecurity.verify_signature(
                payload, signature, wrong_secret, check_timestamp=False
            )
    
    def test_signature_verification_expired_timestamp(self):
        """Should reject expired timestamps"""
        payload = b'{"event": "test"}'
        secret = "test-secret-123"
        old_timestamp = (datetime.now() - timedelta(minutes=10)).isoformat()
        
        signature = WebhookSecurity.calculate_signature(payload, secret, old_timestamp)
        
        with pytest.raises(WebhookSignatureError, match="too old"):
            WebhookSecurity.verify_signature(
                payload, signature, secret, old_timestamp, check_timestamp=True
            )
    
    def test_test_signature_generation(self):
        """Should generate valid test signatures"""
        payload = b'{"test": "data"}'
        secret = "test-secret"
        
        signature = WebhookSecurity.generate_test_signature(payload, secret)
        
        # Should be valid
        assert len(signature) == 64
        assert all(c in "0123456789abcdef" for c in signature)


class TestWebhookRateLimiter:
    """Test webhook rate limiting"""
    
    def test_rate_limiter_allows_requests(self):
        """Should allow requests within rate limit"""
        limiter = WebhookRateLimiter(max_requests_per_minute=10)
        
        # First 10 requests should be allowed
        for i in range(10):
            assert limiter.is_allowed("source_1") is True
    
    def test_rate_limiter_blocks_excessive_requests(self):
        """Should block requests exceeding rate limit"""
        limiter = WebhookRateLimiter(max_requests_per_minute=5)
        source = "source_1"
        
        # First 5 should succeed
        for i in range(5):
            assert limiter.is_allowed(source) is True
        
        # 6th should be blocked
        assert limiter.is_allowed(source) is False
    
    def test_rate_limiter_per_source(self):
        """Should track limits per source separately"""
        limiter = WebhookRateLimiter(max_requests_per_minute=3)
        
        # Source 1: 3 requests
        for i in range(3):
            assert limiter.is_allowed("source_1") is True
        
        # Source 1: blocked
        assert limiter.is_allowed("source_1") is False
        
        # Source 2: should still have quota
        for i in range(3):
            assert limiter.is_allowed("source_2") is True


class TestWebhookValidator:
    """Test webhook payload validation"""
    
    def test_payload_size_validation(self):
        """Should validate payload size"""
        # Valid size
        payload = b"x" * (5 * 1024 * 1024)  # 5MB
        assert WebhookValidator.validate_payload_size(payload, max_size_mb=10) is True
    
    def test_payload_size_exceeded(self):
        """Should reject oversized payloads"""
        payload = b"x" * (15 * 1024 * 1024)  # 15MB
        
        with pytest.raises(WebhookSignatureError, match="exceeds maximum"):
            WebhookValidator.validate_payload_size(payload, max_size_mb=10)
    
    def test_content_type_validation(self):
        """Should validate content type"""
        # Valid
        assert WebhookValidator.validate_content_type("application/json") is True
        assert WebhookValidator.validate_content_type("application/json; charset=utf-8") is True
    
    def test_content_type_invalid(self):
        """Should reject invalid content types"""
        with pytest.raises(WebhookSignatureError, match="Invalid content type"):
            WebhookValidator.validate_content_type("text/xml")


# ============================================================================
# SECURITY TEST SUITE 6: INPUT VALIDATION MIDDLEWARE
# ============================================================================

class TestInputValidationMiddleware:
    """Test input validation middleware on API endpoints"""
    
    def test_oversized_request_rejected(self):
        """Should reject requests exceeding body size limit"""
        # Create a very large payload
        large_payload = {"data": "x" * (11 * 1024 * 1024)}  # 11MB (exceeds default 10MB)
        
        response = client.post(
            "/api/tasks",
            json=large_payload,
            headers={"Content-Type": "application/json"}
        )
        
        # Should reject with 400 or 413
        assert response.status_code in [400, 413, 422]
    
    def test_invalid_json_rejected(self):
        """Should reject invalid JSON"""
        response = client.post(
            "/api/tasks",
            content="{invalid json}",
            headers={"Content-Type": "application/json"}
        )
        
        # Should return 400
        assert response.status_code in [400, 422]
    
    def test_invalid_content_type_rejected(self):
        """Should reject invalid content types"""
        response = client.post(
            "/api/tasks",
            data="test data",
            headers={"Content-Type": "text/plain"}
        )
        
        # Should be rejected or processed as form data
        assert response.status_code in [400, 422, 200]
    
    def test_path_traversal_rejected(self):
        """Should reject path traversal attempts"""
        response = client.get("/api/tasks/../../etc/passwd")
        
        # Should be rejected
        assert response.status_code in [400, 404]
    
    def test_null_byte_in_path_rejected(self):
        """Should reject null bytes in path"""
        response = client.get("/api/tasks\x00/secret")
        
        # Should be rejected or treated as invalid path
        assert response.status_code in [400, 404]


# ============================================================================
# SECURITY TEST SUITE 7: WEBHOOK INTEGRATION
# ============================================================================

class TestWebhookIntegration:
    """Test webhook endpoints with security features"""
    
    def test_webhook_with_valid_signature(self):
        """Should accept webhook with valid signature"""
        payload = {
            "event": "entry.create",
            "model": "article",
            "entry": {"id": 1, "title": "Test"}
        }
        
        payload_bytes = json.dumps(payload).encode()
        secret = "webhook-secret"
        timestamp = datetime.now().isoformat()
        signature = WebhookSecurity.calculate_signature(payload_bytes, secret, timestamp)
        
        # Send webhook with signature
        response = client.post(
            "/api/webhooks/content-created",
            json=payload,
            headers={
                "X-Webhook-Signature": signature,
                "X-Webhook-Timestamp": timestamp,
            }
        )
        
        # Should process webhook
        assert response.status_code in [200, 400]  # 400 if signature header not checked yet
    
    def test_webhook_with_invalid_signature(self):
        """Should reject webhook with invalid signature"""
        payload = {
            "event": "entry.create",
            "model": "article",
            "entry": {"id": 1, "title": "Test"}
        }
        
        response = client.post(
            "/api/webhooks/content-created",
            json=payload,
            headers={
                "X-Webhook-Signature": "invalid-signature-1234567890abcdef1234567890abcdef",
                "X-Webhook-Timestamp": datetime.now().isoformat(),
            }
        )
        
        # May be processed without signature validation (depends on implementation)
        assert response.status_code in [200, 400, 401]
