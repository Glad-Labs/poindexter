"""
Sentry Error Tracking Integration Service

Provides enterprise-grade error tracking, performance monitoring, and issue management
for Poindexter (the AI cofounder pipeline).

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
from typing import Any

from fastapi import FastAPI

from services.logger_config import get_logger

try:
    import sentry_sdk
    from sentry_sdk.integrations.asyncio import AsyncioIntegration
    from sentry_sdk.integrations.fastapi import FastApiIntegration
    from sentry_sdk.integrations.logging import LoggingIntegration
    from sentry_sdk.integrations.starlette import StarletteIntegration
    from sentry_sdk.integrations.threading import ThreadingIntegration

    try:
        from sentry_sdk.integrations.sqlalchemy import SqlAlchemyIntegration  # type: ignore
    except Exception:
        SqlAlchemyIntegration = None  # type: ignore[assignment,misc]

    SENTRY_AVAILABLE = True
except ImportError:
    sentry_sdk = None  # type: ignore[assignment]
    AsyncioIntegration = None  # type: ignore[assignment,misc]
    FastApiIntegration = None  # type: ignore[assignment,misc]
    LoggingIntegration = None  # type: ignore[assignment,misc]
    SqlAlchemyIntegration = None  # type: ignore[assignment,misc]
    StarletteIntegration = None  # type: ignore[assignment,misc]
    ThreadingIntegration = None  # type: ignore[assignment,misc]
    SENTRY_AVAILABLE = False
    logging.warning(
        "Sentry SDK not installed. Error tracking disabled. Install with: pip install sentry-sdk[fastapi]"
    )

logger = get_logger(__name__)


class SentryIntegration:
    """
    Sentry error tracking and performance monitoring integration.

    Handles initialization, configuration, and usage of Sentry SDK for the FastAPI application.
    Provides convenience methods for manual error/event reporting.
    """

    _initialized = False
    _sentry_enabled = False

    @classmethod
    def initialize(
        cls,
        app: FastAPI,  # noqa: ARG003 — main.py passes the app; FastApiIntegration hooks globally, doesn't need the instance
        site_config: Any,
        service_name: str = "cofounder-agent",
    ):
        """
        Initialize Sentry SDK with FastAPI integration.

        Args:
            app: FastAPI application instance
            site_config: SiteConfig instance (DI — Phase H). Must be passed
                explicitly — the module-level singleton import was removed
                so tests can construct isolated mocks and so this initializer
                doesn't read empty values at import time.
            service_name: Name of the service for tracking

        Returns:
            bool: True if Sentry was successfully initialized, False otherwise
        """
        if not SENTRY_AVAILABLE or sentry_sdk is None:
            logger.warning("[ERROR] Sentry SDK not available - error tracking disabled")
            return False

        if cls._initialized:
            logger.debug("Sentry already initialized")
            return cls._sentry_enabled

        # Get configuration from the injected site_config.
        sentry_dsn = site_config.get("sentry_dsn", "").strip()
        sentry_enabled = site_config.get("sentry_enabled", "true").lower() in ("true", "1", "yes")
        environment = site_config.get("environment", "development") or "development"
        release = site_config.get("app_version", "3.0.1")

        # Skip initialization if DSN not configured or explicitly disabled.
        # Do NOT set _initialized here — lifespan re-runs this after site_config
        # loads, and if we latched to "already initialized" on the empty read
        # from a module-level call, the real DSN would never take effect.
        if not sentry_dsn:
            logger.info("[WARNING] Sentry DSN not configured (site_config.sentry_dsn)")
            cls._sentry_enabled = False
            return False

        if not sentry_enabled:
            logger.info("Sentry disabled via SENTRY_ENABLED=false")
            cls._initialized = True
            cls._sentry_enabled = False
            return False

        try:
            integrations = [
                FastApiIntegration(),  # type: ignore[misc]
                StarletteIntegration(),  # type: ignore[misc]
                AsyncioIntegration(),  # type: ignore[misc]
                LoggingIntegration(  # type: ignore[misc]
                    level=logging.INFO,
                    event_level=logging.ERROR,
                ),
                ThreadingIntegration(propagate_hub=True),  # type: ignore[misc]
            ]
            if SqlAlchemyIntegration is not None:
                integrations.append(SqlAlchemyIntegration())  # type: ignore[misc]

            # Debug mode floods stdout with `[sentry] DEBUG:` lines for
            # every traced request — useful when first wiring up the
            # SDK, painful as steady-state log noise. Default off,
            # tunable via app_settings.sentry_debug for the rare cases
            # we want it back.
            sentry_debug = site_config.get_bool("sentry_debug", False)
            sentry_sdk.init(
                dsn=sentry_dsn,
                integrations=integrations,
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
                before_send=cls._before_send,  # type: ignore[arg-type]
                # Include local variables in stack traces
                include_local_variables=True,
                # Error attachment configurations
                max_value_length=4096,  # Max value length for variable inspection
                debug=sentry_debug,
            )

            # Belt and suspenders: even with debug=False the SDK's
            # internal logger occasionally emits at DEBUG (e.g. when
            # samplers reject a transaction). Cap at WARNING so log
            # streams stay readable.
            for _name in ("sentry_sdk", "sentry_sdk.errors"):
                logging.getLogger(_name).setLevel(
                    logging.DEBUG if sentry_debug else logging.WARNING
                )

            # Set user context for authenticated requests (if available)
            sentry_sdk.set_tag("service", service_name)
            sentry_sdk.set_tag("version", release)

            logger.info("[OK] Sentry initialized successfully")
            logger.info("   Environment: %s", environment)
            logger.info("   Release: %s", release)
            logger.info("   Traces Sample Rate: %s", 0.1 if environment == 'production' else 1.0)
            logger.info("   Dashboard: https://sentry.io")

            cls._initialized = True
            cls._sentry_enabled = True
            return True

        except Exception as e:
            logger.error(
                "[_initialize] [ERROR] Failed to initialize Sentry: %s", e, exc_info=True
            )
            cls._initialized = True
            cls._sentry_enabled = False
            return False

    @staticmethod
    def _before_send(event: dict, hint: dict) -> dict | None:
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
        cls, error: Exception, context: dict | None = None, level: str = "error"
    ):
        """
        Manually capture an exception with optional context.

        Args:
            error: The exception to capture
            context: Additional context dictionary
            level: Severity level (fatal, error, warning, info, debug)
        """
        if not cls._sentry_enabled or sentry_sdk is None:
            return

        try:
            with sentry_sdk.push_scope() as scope:
                if context:
                    for key, value in context.items():
                        scope.set_context(key, value)

                scope.set_level(level)  # type: ignore[arg-type]
                sentry_sdk.capture_exception(error)
        except Exception as e:
            logger.error(
                "[_capture_exception] Failed to capture exception in Sentry: %s", e, exc_info=True
            )

    @classmethod
    def capture_message(cls, message: str, level: str = "info", context: dict | None = None):
        """
        Manually capture a message event.

        Args:
            message: The message to capture
            level: Severity level (fatal, error, warning, info, debug)
            context: Additional context dictionary
        """
        if not cls._sentry_enabled or sentry_sdk is None:
            return

        try:
            with sentry_sdk.push_scope() as scope:
                if context:
                    for key, value in context.items():
                        scope.set_context(key, value)

                sentry_sdk.capture_message(message, level=level)  # type: ignore[arg-type]
        except Exception as e:
            logger.error(
                "[_capture_message] Failed to capture message in Sentry: %s", e, exc_info=True
            )

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
        if not cls._sentry_enabled or sentry_sdk is None:
            return

        try:
            sentry_sdk.set_user({"id": user_id, "email": email, "username": username})
        except Exception as e:
            logger.error(
                "[_set_user_context] Failed to set user context in Sentry: %s", e, exc_info=True
            )

    @classmethod
    def clear_user_context(cls):
        """Clear user context after logout."""
        if not cls._sentry_enabled or sentry_sdk is None:
            return

        try:
            sentry_sdk.set_user(None)
        except Exception as e:
            logger.error(
                "[_clear_user_context] Failed to clear user context in Sentry: %s", e, exc_info=True
            )

    @classmethod
    def add_breadcrumb(
        cls, category: str, message: str, level: str = "info", data: dict | None = None
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
        if not cls._sentry_enabled or sentry_sdk is None:
            return

        try:
            sentry_sdk.add_breadcrumb(
                category=category, message=message, level=level, data=data or {}
            )
        except Exception as e:
            logger.error(
                "[_add_breadcrumb] Failed to add breadcrumb in Sentry: %s", e, exc_info=True
            )

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
        if not cls._sentry_enabled or sentry_sdk is None:
            return None

        try:
            return sentry_sdk.start_transaction(name=name, op=op, description=description)
        except Exception as e:
            logger.error(
                "[_start_transaction] Failed to start Sentry transaction: %s", e, exc_info=True
            )
            return None

    @classmethod
    def get_initialized_status(cls) -> bool:
        """Check if Sentry is enabled and initialized."""
        return cls._sentry_enabled


def setup_sentry(
    app: FastAPI,
    site_config: Any,
    service_name: str = "cofounder-agent",
) -> bool:
    """
    Convenience function to initialize Sentry.

    Usage in main.py:
        from services.sentry_integration import setup_sentry
        from services.site_config import site_config
        setup_sentry(app, site_config)

    Args:
        app: FastAPI application instance
        site_config: SiteConfig instance (DI — Phase H)
        service_name: Name of the service

    Returns:
        bool: True if successfully initialized
    """
    return SentryIntegration.initialize(app, site_config, service_name)
