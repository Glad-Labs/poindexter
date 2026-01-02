import logging
from logging.handlers import RotatingFileHandler
import os
from typing import Optional
import importlib

# Optional dependency: python-json-logger (avoid direct import to satisfy static analyzers)
jsonlogger = None
try:  # pragma: no cover - exercised via tests
    _mod = importlib.import_module("pythonjsonlogger.jsonlogger")
    jsonlogger = getattr(_mod, "jsonlogger", _mod)
except Exception:  # pragma: no cover
    jsonlogger = None

# Firestore logging handler imported lazily in setup_logging to avoid heavy deps at import time.
# from utils.firestore_logger import FirestoreLogHandler
# from services.firestore_client import FirestoreClient


def _make_formatter() -> logging.Formatter:
    """Return a JSON formatter when available, otherwise a plain formatter."""
    if jsonlogger is not None:
        return jsonlogger.JsonFormatter("%(asctime)s %(name)s %(levelname)s %(message)s")
    return logging.Formatter("%(asctime)s %(name)s %(levelname)s %(message)s")


def setup_logging(firestore_client: Optional[object] = None):
    """
    Configures the logging for the application to output structured JSON logs
    and stream logs to Firestore.
    """
    log_dir = "logs"
    if not os.path.exists(log_dir):
        os.makedirs(log_dir)

    log_file = os.path.join(log_dir, "app.log")

    # Get the root logger
    logger = logging.getLogger()
    logger.setLevel(logging.INFO)

    # Remove any existing handlers to avoid duplicate logs
    if logger.hasHandlers():
        logger.handlers.clear()

    # Create a file handler that logs messages to a file
    file_handler = RotatingFileHandler(
        log_file, maxBytes=1024 * 1024 * 5, backupCount=5
    )

    # Create formatter (JSON if available)
    formatter = _make_formatter()

    file_handler.setFormatter(formatter)
    logger.addHandler(file_handler)

    # Add a handler for Firestore logging if a client is provided
    if firestore_client:
        # Lazy import to avoid requiring google.cloud during import or unit tests
        try:
            from utils.firestore_logger import FirestoreLogHandler  # type: ignore
        except Exception:  # pragma: no cover - environment without GCP libs
            FirestoreLogHandler = None
        if FirestoreLogHandler is not None:
            handler = FirestoreLogHandler(firestore_client)  # type: ignore[arg-type]
            handler.setLevel(logging.INFO)
            logger.addHandler(handler)

    # Also add a handler for console output for local development
    console_handler = logging.StreamHandler()
    console_formatter = logging.Formatter(
        "%(asctime)s - %(name)s - %(levelname)s - %(message)s"
    )
    console_handler.setFormatter(console_formatter)
    logger.addHandler(console_handler)

    logging.info("Structured logging configured.")


if __name__ == "__main__":
    setup_logging()
    logging.info("This is an info message.")
    logging.warning("This is a warning message.")
    logging.error("This is an error message.")
