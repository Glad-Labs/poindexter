"""
Sentry Error Tracking Integration Service

Provides enterprise-grade error tracking, performance monitoring, and issue management
for the Glad Labs AI Co-Founder system.

Features:
- Automatic exception capturing and reporting
- Performance monitoring with transaction tracing
- Breadcrumb tracking for debugging context
- FastAPI integration with request/response capture
- Async task monitoring
- Release tracking and version management
- Environment-specific configuration

Configuration:
Set SENTRY_DSN environment variable to enable:
    export SENTRY_DSN="https://key@sentry.io/project-id"

For local development, set SENTRY_ENABLED=false to disable reporting.
"""

import logging
import os
from datetime import datetime
from typing import Optional

from fastapi import FastAPI

try:
    import sentry_sdk
    from sentry_sdk.integrations.asyncio import AsyncioIntegration
    from sentry_sdk.integrations.fastapi import FastApiIntegration
    from sentry_sdk.integrations.logging import LoggingIntegration
    from sentry_sdk.integrations.sqlalchemy import SqlAlchemyIntegration
    from sentry_sdk.integrations.starlette import StarletteIntegration
    from sentry_sdk.integrations.threading import ThreadingIntegration

    SENTRY_AVAILABLE = True
except ImportError:
    SENTRY_AVAILABLE = False
    logging.warning(
        "Sentry SDK not installed. Error tracking disabled. Install with: pip install sentry-sdk[fastapi]"
    )

logger = logging.getLogger(__name__)


class SentryIntegration:
    """
    Sentry error tracking and performance monitoring integration.

    Handles initialization, configuration, and usage of Sentry SDK for the FastAPI application.
    Provides convenience methods for manual error/event reporting.
    """

    _initialized = False
    _sentry_enabled = False

    @classmethod
    def initialize(cls, app: FastAPI, service_name: str = "cofounder-agent"):
        """
        Initialize Sentry SDK with FastAPI integration.

        Args:
            app: FastAPI application instance
            service_name: Name of the service for tracking

        Returns:
            bool: True if Sentry was successfully initialized, False otherwise
        """
        if not SENTRY_AVAILABLE:
            logger.warning("[ERROR] Sentry SDK not available - error tracking disabled")
            return False

        if cls._initialized:
            logger.debug("Sentry already initialized")
            return cls._sentry_enabled

        # Get configuration from environment
        sentry_dsn = os.getenv("SENTRY_DSN", "").strip()
        sentry_enabled = os.getenv("SENTRY_ENABLED", "true").lower() in ("true", "1", "yes")
        environment = os.getenv("ENVIRONMENT", "development")
        release = os.getenv("APP_VERSION", "3.0.1")

        # Skip initialization if DSN not configured or explicitly disabled
        if not sentry_dsn:
            logger.info("[WARNING] Sentry DSN not configured (SENTRY_DSN env var)")
            logger.info(
                "   To enable error tracking, set: export SENTRY_DSN='https://key@sentry.io/project-id'"
            )
            cls._initialized = True
            cls._sentry_enabled = False
            return False

        if not sentry_enabled:
            logger.info("ℹ️  Sentry disabled via SENTRY_ENABLED=false")
            cls._initialized = True
            cls._sentry_enabled = False
            return False

        try:
            # Initialize Sentry with comprehensive integrations
            sentry_sdk.init(
                dsn=sentry_dsn,
                integrations=[
                    # Framework integrations
                    FastApiIntegration(),
                    StarletteIntegration(),
                    AsyncioIntegration(),
                    # Database and ORM integrations
                    SqlAlchemyIntegration(),
                    # Logging integration with custom level
                    LoggingIntegration(
                        level=logging.INFO,  # Capture info level and above
                        event_level=logging.ERROR,  # Send error level events to Sentry
                    ),
                    # Threading integration for background tasks
                    ThreadingIntegration(propagate_hub=True),
                ],
                # Environment and release information
                environment=environment,
                release=release,
                # Performance monitoring configuration
                traces_sample_rate=(
                    0.1 if environment == "production" else 1.0
                ),  # 10% in prod, 100% in dev
                profiles_sample_rate=(
                    0.1 if environment == "production" else 1.0
                ),  # Profile 10% of transactions
                # Before sending event to Sentry (filter sensitive data)
                before_send=cls._before_send,
                # Include local variables in stack traces
                include_local_variables=True,
                # Error attachment configurations
                max_value_length=4096,  # Max value length for variable inspection
                # Enable debug logging in development
                debug=environment == "development",
            )

            # Set user context for authenticated requests (if available)
            sentry_sdk.set_tag("service", service_name)
            sentry_sdk.set_tag("version", release)

            logger.info(f"[OK] Sentry initialized successfully")
            logger.info(f"   Environment: {environment}")
            logger.info(f"   Release: {release}")
            logger.info(f"   Traces Sample Rate: {0.1 if environment == 'production' else 1.0}")
            logger.info(f"   Dashboard: https://sentry.io")

            cls._initialized = True
            cls._sentry_enabled = True
            return True

        except Exception as e:
            logger.error(f"[ERROR] Failed to initialize Sentry: {str(e)}")
            cls._initialized = True
            cls._sentry_enabled = False
            return False

    @staticmethod
    def _before_send(event: dict, hint: dict) -> Optional[dict]:
        """
        Filter events before sending to Sentry.
        Remove sensitive data (passwords, tokens, etc.)

        Args:
            event: The event dictionary
            hint: Additional hint information with exception details

        Returns:
            Modified event dict, or None to drop the event
        """
        # Check if this is an error event we should capture
        if event.get("level") == "error" or (hint and "exc_info" in hint):
            # Redact sensitive headers
            if "request" in event:
                headers = event["request"].get("headers", {})
                sensitive_headers = ["authorization", "cookie", "x-api-key", "x-token"]
                for header in sensitive_headers:
                    if header in headers:
                        headers[header] = "[REDACTED]"

            # Redact sensitive query parameters
            if "request" in event and "url" in event["request"]:
                url = event["request"]["url"]
                if "api_key=" in url or "token=" in url:
                    event["request"]["url"] = url.replace(
                        url[url.find("api_key=") :], "api_key=[REDACTED]"
                    )

        return event

    @classmethod
    def capture_exception(
        cls, error: Exception, context: Optional[dict] = None, level: str = "error"
    ):
        """
        Manually capture an exception with optional context.

        Args:
            error: The exception to capture
            context: Additional context dictionary
            level: Severity level (fatal, error, warning, info, debug)
        """
        if not cls._sentry_enabled:
            return

        try:
            with sentry_sdk.push_scope() as scope:
                if context:
                    for key, value in context.items():
                        scope.set_context(key, value)

                scope.set_level(level)
                sentry_sdk.capture_exception(error)
        except Exception as e:
            logger.debug(f"Failed to capture exception in Sentry: {e}")

    @classmethod
    def capture_message(cls, message: str, level: str = "info", context: Optional[dict] = None):
        """
        Manually capture a message event.

        Args:
            message: The message to capture
            level: Severity level (fatal, error, warning, info, debug)
            context: Additional context dictionary
        """
        if not cls._sentry_enabled:
            return

        try:
            with sentry_sdk.push_scope() as scope:
                if context:
                    for key, value in context.items():
                        scope.set_context(key, value)

                sentry_sdk.capture_message(message, level=level)
        except Exception as e:
            logger.debug(f"Failed to capture message in Sentry: {e}")

    @classmethod
    def set_user_context(cls, user_id: str, email: str = "", username: str = ""):
        """
        Set user context for error tracking.
        Called after authentication to track which user experienced errors.

        Args:
            user_id: Unique user identifier
            email: User email address
            username: User's username
        """
        if not cls._sentry_enabled:
            return

        try:
            sentry_sdk.set_user({"id": user_id, "email": email, "username": username})
        except Exception as e:
            logger.debug(f"Failed to set user context in Sentry: {e}")

    @classmethod
    def clear_user_context(cls):
        """Clear user context after logout."""
        if not cls._sentry_enabled:
            return

        try:
            sentry_sdk.set_user(None)
        except Exception as e:
            logger.debug(f"Failed to clear user context in Sentry: {e}")

    @classmethod
    def add_breadcrumb(
        cls, category: str, message: str, level: str = "info", data: Optional[dict] = None
    ):
        """
        Add a breadcrumb for debugging context.
        Breadcrumbs are captured and sent with errors for better debugging.

        Args:
            category: Breadcrumb category (e.g., "api.call", "database", "auth")
            message: Breadcrumb message
            level: Severity level (critical, error, warning, info, debug)
            data: Additional data dictionary
        """
        if not cls._sentry_enabled:
            return

        try:
            sentry_sdk.add_breadcrumb(
                category=category, message=message, level=level, data=data or {}
            )
        except Exception as e:
            logger.debug(f"Failed to add breadcrumb in Sentry: {e}")

    @classmethod
    def start_transaction(cls, name: str, op: str = "http.request", description: str = ""):
        """
        Start a performance monitoring transaction.

        Args:
            name: Transaction name
            op: Operation type (http.request, task, function, etc.)
            description: Human-readable description

        Returns:
            Sentry transaction object or None
        """
        if not cls._sentry_enabled:
            return None

        try:
            return sentry_sdk.start_transaction(name=name, op=op, description=description)
        except Exception as e:
            logger.debug(f"Failed to start Sentry transaction: {e}")
            return None

    @classmethod
    def get_initialized_status(cls) -> bool:
        """Check if Sentry is enabled and initialized."""
        return cls._sentry_enabled


def setup_sentry(app: FastAPI, service_name: str = "cofounder-agent") -> bool:
    """
    Convenience function to initialize Sentry.

    Usage in main.py:
        from services.sentry_integration import setup_sentry
        setup_sentry(app)

    Args:
        app: FastAPI application instance
        service_name: Name of the service

    Returns:
        bool: True if successfully initialized
    """
    return SentryIntegration.initialize(app, service_name)
