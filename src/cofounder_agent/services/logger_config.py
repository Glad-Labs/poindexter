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

import os
import logging
import sys
from typing import Optional

# Try to import structlog for structured logging support
try:
    import structlog

    STRUCTLOG_AVAILABLE = True
except ImportError:
    STRUCTLOG_AVAILABLE = False

# ============================================================================
# CONFIGURATION
# ============================================================================

ENVIRONMENT = os.getenv("ENVIRONMENT", "development")
LOG_LEVEL = os.getenv("LOG_LEVEL", "INFO").upper()
LOG_FORMAT = os.getenv("LOG_FORMAT", "json" if ENVIRONMENT == "production" else "text")

# Validate log level
VALID_LOG_LEVELS = {"DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"}
if LOG_LEVEL not in VALID_LOG_LEVELS:
    LOG_LEVEL = "INFO"


# ============================================================================
# STRUCTURED LOGGING CONFIGURATION (Primary)
# ============================================================================


def configure_structlog() -> bool:
    """
    Configure structlog for structured JSON logging.
    Returns True if successful, False if structlog unavailable.
    """
    if not STRUCTLOG_AVAILABLE:
        return False

    try:
        structlog.configure(
            processors=[
                # Filter by log level
                structlog.stdlib.filter_by_level,
                # Add context information
                structlog.stdlib.add_logger_name,
                structlog.stdlib.add_log_level,
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
        # JSON format for production
        log_format = (
            '{"timestamp": "%(asctime)s", '
            '"level": "%(levelname)s", '
            '"logger": "%(name)s", '
            '"message": "%(message)s"}'
        )
    else:
        # Human-readable format for development
        log_format = "%(asctime)s - %(name)s - %(levelname)s - %(message)s"

    # Configure root logger
    logging.basicConfig(
        level=getattr(logging, LOG_LEVEL),
        format=log_format,
        handlers=[
            logging.StreamHandler(sys.stdout),
        ],
    )


# ============================================================================
# INITIALIZATION
# ============================================================================

# Try to configure structlog first, fall back to standard logging
_structlog_configured = configure_structlog()
if not _structlog_configured:
    configure_standard_logging()


# ============================================================================
# PUBLIC API - UNIFIED LOGGER GETTER
# ============================================================================


def get_logger(name: Optional[str] = None):
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
    if STRUCTLOG_AVAILABLE and _structlog_configured:
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

    if STRUCTLOG_AVAILABLE and _structlog_configured:
        # For structlog, we need to update the processors
        # This is a simplified approach - a full implementation would be more complex
        structlog.configure(
            processors=[
                structlog.stdlib.filter_by_level,
                structlog.stdlib.add_logger_name,
                structlog.stdlib.add_log_level,
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
# DEPRECATED API - For backward compatibility only
# ============================================================================


def get_standard_logger(name: Optional[str] = None):
    """
    DEPRECATED: Use get_logger() instead.

    Gets a standard (non-structured) logger.
    This function is maintained for backward compatibility only.
    """
    return logging.getLogger(name)


# ============================================================================
# MODULE INITIALIZATION INFO
# ============================================================================

if __name__ == "__main__":
    # Show configuration when module is run directly
    logger = get_logger("logger_config")
    logger.info(
        "Logger configuration initialized",
        environment=ENVIRONMENT,
        log_level=LOG_LEVEL,
        log_format=LOG_FORMAT,
        structlog_available=STRUCTLOG_AVAILABLE,
        using_structlog=_structlog_configured,
    )
