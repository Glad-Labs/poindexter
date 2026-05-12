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
import re
import sys
from logging.handlers import RotatingFileHandler
from pathlib import Path
from typing import Any

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
    _logger: logging.Logger, _method_name: str, event_dict: dict
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


# ---------------------------------------------------------------------------
# Secret redaction processor (audit-2026-05-12 P1 #12)
# ---------------------------------------------------------------------------
#
# Two layers of defense against accidentally logging secrets:
#
# 1. KEY-based redaction. Any event_dict key whose NAME matches
#    SECRET_KEY_PATTERN gets its value replaced with REDACTED_VALUE,
#    regardless of the value's shape.
#
# 2. VALUE-shape detection. Any *string* value that looks like a credential
#    on its face — "Bearer ...", Langfuse keys, Poindexter-prefixed
#    secrets, the envelope-encryption sentinel — gets redacted even when
#    the key name is something innocuous like "data" or "value". Catches
#    the "loaded auth header into a generic field" pattern.
#
# Both walks are recursive (dicts AND lists) with a depth cap to avoid
# pathological inputs. Any exception inside the redactor is swallowed —
# we'd rather log unredacted than crash the logger.
#
# To add a new secret-key family: extend SECRET_KEY_PATTERN with another
# alternative. To detect a new credential value shape: extend
# SECRET_VALUE_PREFIXES (literal prefix) or SECRET_VALUE_PATTERN (regex).

REDACTED_VALUE = "***REDACTED***"
_MAX_REDACTION_DEPTH = 5

# Keys whose values should ALWAYS be masked, by name (case-insensitive,
# matched against the whole key — `re.search` so suffixed variants like
# `x_api_key_header` and `discord_ops_webhook_url` are caught).
SECRET_KEY_PATTERN = re.compile(
    r"(?i)("
    r"token|secret|password|api_key|api-key|authorization|cookie|"
    r"bearer|dsn|signing_key|signing-key|access_key|access-key|"
    r"client_secret|client-secret|webhook_url|webhook-url|"
    r"x[-_]revalidate[-_]secret|x[-_]api[-_]key|langfuse_secret|"
    r"discord_ops_webhook|indexnow_key"
    r")"
)

# String VALUES that obviously look like a credential, regardless of which
# key they appeared under. Catches `logger.info("loaded", data=bearer_hdr)`.
SECRET_VALUE_PREFIXES: tuple[str, ...] = (
    "Bearer ",
    "bearer ",
    "pk-lf-",  # Langfuse public key
    "sk-lf-",  # Langfuse secret key
    "pdx_",  # Poindexter-issued tokens
    "enc:v1:",  # plugins/secrets.py envelope-encryption sentinel
    "sk-",  # OpenAI-style
    "ghp_",  # GitHub personal access token
    "gho_",  # GitHub OAuth token
    "xoxb-",  # Slack bot token
    "xoxp-",  # Slack user token
)

# Regex form for prefixes that need anchored matching beyond a simple
# `startswith`. Currently empty — every shape today is a literal prefix —
# but the slot is here so adding shape-rules doesn't require restructuring.
SECRET_VALUE_PATTERN = re.compile(r"^(?:Basic [A-Za-z0-9+/=]{8,})$")


def _looks_like_secret_value(value: Any) -> bool:
    """Return True if a value's *shape* alone says it's a credential."""
    if not isinstance(value, str):
        return False
    if not value:
        return False
    for prefix in SECRET_VALUE_PREFIXES:
        if value.startswith(prefix):
            return True
    if SECRET_VALUE_PATTERN.match(value):
        return True
    return False


def _redact_walk(obj: Any, depth: int = 0) -> Any:
    """Recursively walk dict/list trees, returning a redacted copy.

    Strings/scalars are returned as-is (they're already at the leaf —
    the caller's KEY decides whether to mask). The depth cap prevents
    blowing the stack on circular references or pathologically deep
    payloads; once the cap is hit we just return the subtree unchanged
    (degraded redaction is still better than crashing the logger).
    """
    if depth >= _MAX_REDACTION_DEPTH:
        return obj

    if isinstance(obj, dict):
        out: dict[Any, Any] = {}
        for k, v in obj.items():
            if isinstance(k, str) and SECRET_KEY_PATTERN.search(k):
                out[k] = REDACTED_VALUE
            elif _looks_like_secret_value(v):
                out[k] = REDACTED_VALUE
            elif isinstance(v, (dict, list)):
                out[k] = _redact_walk(v, depth + 1)
            else:
                out[k] = v
        return out

    if isinstance(obj, list):
        return [
            REDACTED_VALUE
            if _looks_like_secret_value(item)
            else (
                _redact_walk(item, depth + 1)
                if isinstance(item, (dict, list))
                else item
            )
            for item in obj
        ]

    return obj


def redact_secrets(
    _logger: Any, _method_name: str, event_dict: dict
) -> dict:
    """Structlog processor that masks secret keys + secret-looking values.

    Mounted between the timestamper and the renderer in the structlog
    processor chain. Any exception is swallowed (logged once to stderr)
    so a bug in the redactor can never crash the logger.
    """
    try:
        redacted: dict[Any, Any] = {}
        for k, v in event_dict.items():
            if isinstance(k, str) and SECRET_KEY_PATTERN.search(k):
                redacted[k] = REDACTED_VALUE
            elif _looks_like_secret_value(v):
                redacted[k] = REDACTED_VALUE
            elif isinstance(v, (dict, list)):
                redacted[k] = _redact_walk(v, depth=1)
            else:
                redacted[k] = v
        return redacted
    except Exception as exc:  # pragma: no cover - defensive
        # Emit a single stderr line and fall back to the un-redacted dict.
        # Crashing the logger is worse than logging an unredacted line.
        try:
            print(
                f"Warning: secret-redaction processor failed: {exc}",
                file=sys.stderr,
            )
        except Exception:
            pass
        return event_dict


class SecretRedactionFilter(logging.Filter):
    """Stdlib `logging.Filter` mirror of `redact_secrets`.

    Walks `record.args` (positional `%`-args used by stdlib formatters)
    and `record.__dict__` extras for any keys/values matching the secret
    rules above. Mounted on every handler in `configure_standard_logging`
    so third-party libraries that log via stdlib (uvicorn, httpx, asyncpg,
    etc.) get the same masking treatment as our structlog calls.
    """

    def filter(self, record: logging.LogRecord) -> bool:
        try:
            # Redact extras attached via `logger.info("msg", extra={...})`.
            # These show up as attributes on `record`. Iterate a snapshot
            # so we can mutate during iteration.
            for attr_name in list(record.__dict__.keys()):
                # Skip the stdlib-internal attrs — they're not user data.
                if attr_name in _STDLIB_LOGRECORD_ATTRS:
                    continue
                value = record.__dict__[attr_name]
                if SECRET_KEY_PATTERN.search(attr_name):
                    record.__dict__[attr_name] = REDACTED_VALUE
                elif _looks_like_secret_value(value):
                    record.__dict__[attr_name] = REDACTED_VALUE
                elif isinstance(value, (dict, list)):
                    record.__dict__[attr_name] = _redact_walk(value, depth=1)

            # Redact positional `%`-args. Dict-shaped args get walked;
            # tuple/list-shaped args have value-shape detection applied
            # element-wise.
            if record.args:
                if isinstance(record.args, dict):
                    record.args = _redact_walk(record.args, depth=0)
                elif isinstance(record.args, tuple):
                    record.args = tuple(
                        REDACTED_VALUE
                        if _looks_like_secret_value(a)
                        else (
                            _redact_walk(a, depth=1)
                            if isinstance(a, (dict, list))
                            else a
                        )
                        for a in record.args
                    )
        except Exception as exc:  # pragma: no cover - defensive
            try:
                print(
                    f"Warning: SecretRedactionFilter failed: {exc}",
                    file=sys.stderr,
                )
            except Exception:
                pass
        # Always allow the record through.
        return True


# The stdlib LogRecord attributes we should NOT treat as user-supplied data.
# Sourced from cpython logging.LogRecord.__init__.
_STDLIB_LOGRECORD_ATTRS = frozenset(
    {
        "name",
        "msg",
        "args",
        "levelname",
        "levelno",
        "pathname",
        "filename",
        "module",
        "exc_info",
        "exc_text",
        "stack_info",
        "lineno",
        "funcName",
        "created",
        "msecs",
        "relativeCreated",
        "thread",
        "threadName",
        "processName",
        "process",
        "taskName",
        "message",
        "asctime",
        # Our own request-id filter attribute — already safe.
        "request_id",
    }
)


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
                # Mask secret-named keys and bearer-shaped values
                # (audit-2026-05-12 P1 #12). Runs after the timestamper
                # so internal `_record`-style keys are already in place,
                # and before the renderer so the masked dict is what
                # actually hits stdout / Loki / disk.
                redact_secrets,
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

    # Apply the request-ID-aware formatter to every handler. Also attach
    # the stdlib secret-redaction filter (audit-2026-05-12 P1 #12) so any
    # third-party library that logs via stdlib (uvicorn, httpx, asyncpg,
    # etc.) gets the same secret masking as our structlog calls.
    formatter = _RequestIDFormatter(log_format)
    redaction_filter = SecretRedactionFilter()
    for handler in handlers:
        handler.setFormatter(formatter)
        handler.addFilter(redaction_filter)

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
                redact_secrets,
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
