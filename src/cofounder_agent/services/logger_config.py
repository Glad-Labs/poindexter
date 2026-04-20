"""
Centralized Logging Configuration for Glad Labs AI Co-Founder

This module provides unified logging configuration across the entire application.
Supports both structured logging (structlog) and standard logging.

Environment Variables:
    LOG_LEVEL: Set logging level (DEBUG, INFO, WARNING, ERROR, CRITICAL)
               Default: INFO
    LOG_FORMAT: Choose log format (json, text)
               Default: json for production, text for development
    ENVIRONMENT: Deployment environment (development, staging, production)
               Default: development

Usage:
    In any module, instead of:
        import logging
        logger = logging.getLogger(__name__)

    Use:
        from services.logger_config import get_logger
        logger = get_logger(__name__)

This ensures all loggers use the centralized configuration.
"""

import logging
import os
import sys
from logging.handlers import RotatingFileHandler
from pathlib import Path

# Try to import structlog for structured logging support
try:
    import structlog  # type: ignore[import-untyped]
except ImportError:
    structlog = None  # type: ignore[assignment]

STRUCTLOG_AVAILABLE = structlog is not None

# ============================================================================
# CONFIGURATION
# ============================================================================

ENVIRONMENT = os.getenv("ENVIRONMENT", "development")
LOG_LEVEL = os.getenv("LOG_LEVEL", "INFO").upper()
LOG_FORMAT = os.getenv("LOG_FORMAT", "json" if ENVIRONMENT == "production" else "text")
LOG_DIR = Path(os.getenv("LOG_DIR", str(Path(__file__).resolve().parent.parent / "logs")))
LOG_FILE_NAME = os.getenv("LOG_FILE_NAME", "cofounder_agent.log")


def _safe_int_env(name: str, default: int) -> int:
    """Get integer environment variable with safe fallback."""
    value = os.getenv(name)
    if value is None:
        return default
    try:
        return int(value)
    except ValueError:
        return default


LOG_MAX_BYTES = _safe_int_env("LOG_MAX_BYTES", 10 * 1024 * 1024)
LOG_BACKUP_COUNT = _safe_int_env("LOG_BACKUP_COUNT", 10)
LOG_TO_FILE = os.getenv("LOG_TO_FILE", "true").lower() == "true"

# Validate log level
VALID_LOG_LEVELS = {"DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"}
if LOG_LEVEL not in VALID_LOG_LEVELS:
    LOG_LEVEL = "INFO"


# ============================================================================
# STRUCTURED LOGGING CONFIGURATION (Primary)
# ============================================================================


def _add_request_id(
    logger: logging.Logger, method_name: str, event_dict: dict
) -> dict:
    """
    Structlog processor that injects the current request ID into every log event.

    Reads from the ``_request_id_var`` ContextVar set by
    ``middleware.request_id.RequestIDMiddleware``.  When no request is active
    (startup, background workers, etc.) the field is set to ``"-"`` so log
    parsers can always rely on its presence.
    """
    # Import lazily to avoid circular imports at module-load time.
    try:
        from middleware.request_id import _request_id_var
    except ImportError:
        event_dict.setdefault("request_id", "-")
        return event_dict
    event_dict.setdefault("request_id", _request_id_var.get() or "-")
    return event_dict


def configure_structlog() -> bool:
    """
    Configure structlog for structured JSON logging.
    Returns True if successful, False if structlog unavailable.
    """
    if structlog is None:
        return False

    try:
        structlog.configure(
            processors=[
                # Filter by log level
                structlog.stdlib.filter_by_level,
                # Add context information
                structlog.stdlib.add_logger_name,
                structlog.stdlib.add_log_level,
                # Inject current request ID from contextvar
                _add_request_id,
                # Format positional arguments
                structlog.stdlib.PositionalArgumentsFormatter(),
                # Add timestamps in ISO format
                structlog.processors.TimeStamper(fmt="ISO"),
                # Include stack information for exceptions
                structlog.processors.StackInfoRenderer(),
                # Format exception information
                structlog.processors.format_exc_info,
                # Decode unicode properly
                structlog.processors.UnicodeDecoder(),
                # Output as JSON for production, plain text for development
                (
                    structlog.processors.JSONRenderer()
                    if LOG_FORMAT == "json"
                    else structlog.dev.ConsoleRenderer()
                ),
            ],
            context_class=dict,
            logger_factory=structlog.stdlib.LoggerFactory(),
            wrapper_class=structlog.stdlib.BoundLogger,
            cache_logger_on_first_use=True,
        )
        return True
    except Exception as e:
        print(f"Warning: Failed to configure structlog: {e}", file=sys.stderr)
        return False


# ============================================================================
# STANDARD LOGGING CONFIGURATION (Fallback)
# ============================================================================


def configure_standard_logging() -> None:
    """
    Configure standard Python logging as a fallback.
    Used when structlog is not available or is disabled.
    """
    # Define format based on environment
    if LOG_FORMAT == "json":
        # JSON format for production — includes request_id for log correlation.
        # request_id is injected by middleware.request_id.RequestIDFilter;
        # it defaults to '-' when no request is active (e.g., startup/shutdown).
        log_format = (
            '{"timestamp": "%(asctime)s", '
            '"level": "%(levelname)s", '
            '"logger": "%(name)s", '
            '"request_id": "%(request_id)s", '
            '"message": "%(message)s"}'
        )
    else:
        # Human-readable format for development
        log_format = "%(asctime)s [%(request_id)s] %(name)s %(levelname)s - %(message)s"

    class _RequestIDFormatter(logging.Formatter):
        """Formatter that supplies a '-' request_id when the filter hasn't run."""

        def format(self, record: logging.LogRecord) -> str:
            if not hasattr(record, "request_id"):
                record.request_id = "-"
            return super().format(record)

    # Use a UTF-8 stream to avoid UnicodeEncodeError on Windows (cp1252)
    # when log messages contain emoji or other non-ASCII characters.
    # Under pythonw.exe, stdout is None — skip console handler entirely.
    handlers: list[logging.Handler] = []
    if sys.stdout is not None:
        utf8_stream = open(sys.stdout.fileno(), mode="w", encoding="utf-8", closefd=False)
        handlers.append(logging.StreamHandler(utf8_stream))

    if LOG_TO_FILE:
        try:
            LOG_DIR.mkdir(parents=True, exist_ok=True)
            file_handler = RotatingFileHandler(
                LOG_DIR / LOG_FILE_NAME,
                maxBytes=LOG_MAX_BYTES,
                backupCount=LOG_BACKUP_COUNT,
                encoding="utf-8",
            )
            handlers.append(file_handler)
        except Exception as e:
            print(f"Warning: Failed to configure rotating file logging: {e}", file=sys.stderr)

    # Apply the request-ID-aware formatter to every handler
    formatter = _RequestIDFormatter(log_format)
    for handler in handlers:
        handler.setFormatter(formatter)

    # Configure root logger
    root = logging.getLogger()
    root.setLevel(getattr(logging, LOG_LEVEL))
    # Remove any handlers added by previous basicConfig calls
    root.handlers.clear()
    for handler in handlers:
        root.addHandler(handler)


# ============================================================================
# INITIALIZATION
# ============================================================================

# Always configure standard logging handlers first (stdout + rotating files).
# Structlog (if available) then wraps standard logging for structured output.
configure_standard_logging()
_structlog_configured = configure_structlog()


# ============================================================================
# PUBLIC API - UNIFIED LOGGER GETTER
# ============================================================================


def get_logger(name: str | None = None):
    """
    Get a logger instance with centralized configuration.

    This is the RECOMMENDED way to get loggers throughout the application.

    Args:
        name: Logger name (typically __name__ for module loggers)
              If None, returns root logger

    Returns:
        Logger instance (structlog or standard logging)

    Example:
        from services.logger_config import get_logger
        logger = get_logger(__name__)
        logger.info("Starting application")
        logger.error("Something went wrong", error=exc)

    Note:
        When using structlog, you can pass additional context:
        logger = logger.bind(user_id=123)
        logger.info("user_action", action="login")
    """
    if structlog is not None and _structlog_configured:
        return structlog.get_logger(name)
    else:
        return logging.getLogger(name)


def set_log_level(level: str) -> None:
    """
    Dynamically change the log level at runtime.

    Args:
        level: One of DEBUG, INFO, WARNING, ERROR, CRITICAL

    Example:
        from services.logger_config import set_log_level
        set_log_level("DEBUG")  # Enable debug logging
    """
    level_upper = level.upper()
    if level_upper not in VALID_LOG_LEVELS:
        raise ValueError(f"Invalid log level: {level}. Must be one of {VALID_LOG_LEVELS}")

    if structlog is not None and _structlog_configured:
        # For structlog, we need to update the processors
        # This is a simplified approach - a full implementation would be more complex
        structlog.configure(
            processors=[
                structlog.stdlib.filter_by_level,
                structlog.stdlib.add_logger_name,
                structlog.stdlib.add_log_level,
                _add_request_id,
                structlog.stdlib.PositionalArgumentsFormatter(),
                structlog.processors.TimeStamper(fmt="ISO"),
                structlog.processors.StackInfoRenderer(),
                structlog.processors.format_exc_info,
                structlog.processors.UnicodeDecoder(),
                (
                    structlog.processors.JSONRenderer()
                    if LOG_FORMAT == "json"
                    else structlog.dev.ConsoleRenderer()
                ),
            ],
            context_class=dict,
            logger_factory=structlog.stdlib.LoggerFactory(),
            wrapper_class=structlog.stdlib.BoundLogger,
            cache_logger_on_first_use=True,
        )
    else:
        # For standard logging
        logging.getLogger().setLevel(getattr(logging, level_upper))


# ============================================================================
# DEPRECATED API REMOVED - Use get_logger() for all logging needs
# ============================================================================


# ============================================================================
# MODULE INITIALIZATION INFO
# ============================================================================

if __name__ == "__main__":
    # Show configuration when module is run directly
    logger = get_logger("logger_config")
    logger.info(  # type: ignore[call-arg]
        "Logger configuration initialized",
        environment=ENVIRONMENT,  # type: ignore[call-arg]
        log_level=LOG_LEVEL,  # type: ignore[call-arg]
        log_format=LOG_FORMAT,  # type: ignore[call-arg]
        structlog_available=STRUCTLOG_AVAILABLE,  # type: ignore[call-arg]
        using_structlog=_structlog_configured,  # type: ignore[call-arg]
    )
