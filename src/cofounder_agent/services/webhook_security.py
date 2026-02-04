"""
Webhook Security Service

Provides HMAC-SHA256 signature verification for webhook security.
Prevents webhook spoofing and ensures integrity of webhook payloads.
"""

import hashlib
import hmac
import logging
from datetime import datetime, timedelta
from typing import Optional

logger = logging.getLogger(__name__)


class WebhookSignatureError(Exception):
    """Exception raised when webhook signature verification fails"""

    pass


class WebhookSecurity:
    """Provides webhook security utilities including signature verification"""

    # Signature validity window (prevent replay attacks)
    SIGNATURE_VALIDITY_SECONDS = 300  # 5 minutes

    # Header names
    SIGNATURE_HEADER = "X-Webhook-Signature"
    TIMESTAMP_HEADER = "X-Webhook-Timestamp"

    @classmethod
    def verify_signature(
        cls,
        payload: bytes,
        signature: str,
        webhook_secret: str,
        timestamp: Optional[str] = None,
        check_timestamp: bool = True,
    ) -> bool:
        """
        Verify webhook signature using HMAC-SHA256.

        Standard webhook signature format:
            signature = HMAC-SHA256(secret, timestamp + "." + payload_body)

        This prevents:
        - Webhook spoofing (attacker can't create valid signatures)
        - Payload tampering (any modification invalidates signature)
        - Replay attacks (timestamp check prevents old requests)

        Args:
            payload: Raw payload bytes
            signature: Signature from webhook header (hex format)
            webhook_secret: Secret key shared with webhook provider
            timestamp: Timestamp from webhook header (ISO format)
            check_timestamp: Whether to verify timestamp is recent

        Returns:
            True if signature is valid, False otherwise

        Raises:
            WebhookSignatureError: If signature is invalid or timestamp expired
        """
        try:
            # Verify signature format
            if not signature or not isinstance(signature, str):
                raise WebhookSignatureError("Signature is missing or invalid")

            if len(signature) != 64:  # SHA256 hex is 64 characters
                raise WebhookSignatureError("Signature has invalid length")

            # Verify timestamp if provided
            if check_timestamp and timestamp:
                try:
                    ts = datetime.fromisoformat(timestamp.replace("Z", "+00:00"))
                    now = datetime.now(tz=ts.tzinfo) if ts.tzinfo else datetime.now()
                    age = now - ts

                    if age.total_seconds() > cls.SIGNATURE_VALIDITY_SECONDS:
                        raise WebhookSignatureError(
                            f"Webhook timestamp is too old ({age.total_seconds()}s > "
                            f"{cls.SIGNATURE_VALIDITY_SECONDS}s)"
                        )
                except ValueError:
                    raise WebhookSignatureError("Invalid timestamp format")

            # Construct signed content
            if timestamp:
                signed_content = f"{timestamp}.".encode() + payload
            else:
                signed_content = payload

            # Calculate expected signature
            expected_signature = cls._calculate_signature(signed_content, webhook_secret)

            # Compare signatures using constant-time comparison
            # (prevents timing attacks)
            if not hmac.compare_digest(signature.lower(), expected_signature.lower()):
                raise WebhookSignatureError("Signature verification failed")

            return True

        except WebhookSignatureError:
            raise
        except Exception as e:
            raise WebhookSignatureError(f"Signature verification error: {str(e)}")

    @classmethod
    def calculate_signature(
        cls,
        payload: bytes,
        webhook_secret: str,
        timestamp: Optional[str] = None,
    ) -> str:
        """
        Calculate HMAC-SHA256 signature for a webhook payload.

        This is used by webhook providers to sign payloads.

        Args:
            payload: Raw payload bytes
            webhook_secret: Secret key
            timestamp: Optional timestamp to include in signature

        Returns:
            Signature in hexadecimal format
        """
        if timestamp:
            signed_content = f"{timestamp}.".encode() + payload
        else:
            signed_content = payload

        return cls._calculate_signature(signed_content, webhook_secret)

    @classmethod
    def _calculate_signature(cls, content: bytes, secret: str) -> str:
        """Internal method to calculate HMAC-SHA256 signature"""
        if isinstance(secret, str):
            secret = secret.encode()

        signature = hmac.new(secret, content, hashlib.sha256).hexdigest()

        return signature

    @classmethod
    def generate_test_signature(
        cls,
        payload: bytes,
        webhook_secret: str,
    ) -> str:
        """
        Generate a valid test signature for webhook testing.

        Useful for testing webhook endpoints without actual webhook provider.

        Args:
            payload: Test payload bytes
            webhook_secret: Secret key

        Returns:
            Valid HMAC-SHA256 signature
        """
        timestamp = datetime.now().isoformat()
        return cls.calculate_signature(payload, webhook_secret, timestamp)


class WebhookRateLimiter:
    """Rate limiter for webhook endpoints to prevent abuse"""

    def __init__(self, max_requests_per_minute: int = 60):
        """
        Initialize rate limiter.

        Args:
            max_requests_per_minute: Maximum webhook requests per minute per source
        """
        self.max_requests = max_requests_per_minute
        self.requests = {}  # source_id -> [(timestamp, ...)]

    def is_allowed(self, source_id: str) -> bool:
        """
        Check if webhook request from source is allowed.

        Args:
            source_id: Identifier for webhook source (e.g., IP address, account ID)

        Returns:
            True if request is allowed, False if rate limit exceeded
        """
        now = datetime.now()
        one_minute_ago = now - timedelta(minutes=1)

        # Clean old requests
        if source_id in self.requests:
            self.requests[source_id] = [
                ts for ts in self.requests[source_id] if ts > one_minute_ago
            ]

        # Check limit
        if source_id not in self.requests:
            self.requests[source_id] = []

        if len(self.requests[source_id]) >= self.max_requests:
            return False

        # Add current request
        self.requests[source_id].append(now)
        return True


class WebhookValidator:
    """Validates webhook payloads for common issues"""

    @staticmethod
    def validate_payload_size(payload: bytes, max_size_mb: int = 10) -> bool:
        """
        Validate webhook payload size.

        Args:
            payload: Payload bytes
            max_size_mb: Maximum size in megabytes

        Returns:
            True if payload is acceptable size

        Raises:
            WebhookSignatureError: If payload exceeds max size
        """
        max_bytes = max_size_mb * 1024 * 1024
        if len(payload) > max_bytes:
            raise WebhookSignatureError(f"Webhook payload exceeds maximum size of {max_size_mb}MB")
        return True

    @staticmethod
    def validate_content_type(content_type: str, allowed_types: Optional[list] = None) -> bool:
        """
        Validate webhook content type.

        Args:
            content_type: Content-Type header value
            allowed_types: List of allowed content types

        Returns:
            True if content type is allowed

        Raises:
            WebhookSignatureError: If content type is not allowed
        """
        if allowed_types is None:
            allowed_types = [
                "application/json",
                "application/x-www-form-urlencoded",
            ]

        # Extract MIME type (ignore charset)
        mime_type = content_type.split(";")[0].strip()

        if mime_type not in allowed_types:
            raise WebhookSignatureError(
                f"Invalid content type: {mime_type}. " f"Allowed types: {', '.join(allowed_types)}"
            )

        return True
