import logging
from logging.handlers import RotatingFileHandler
import os
from pythonjsonlogger import jsonlogger
from utils.firestore_logger import FirestoreLogHandler
from services.firestore_client import FirestoreClient
from typing import Optional


def setup_logging(firestore_client: Optional[FirestoreClient] = None):
    """
    Configures the logging for the application to output structured JSON logs
    and stream logs to Firestore.
    """
    log_dir = 'logs'
    if not os.path.exists(log_dir):
        os.makedirs(log_dir)

    log_file = os.path.join(log_dir, 'app.log')

    # Get the root logger
    logger = logging.getLogger()
    logger.setLevel(logging.INFO)

    # Remove any existing handlers to avoid duplicate logs
    if logger.hasHandlers():
        logger.handlers.clear()

    # Create a file handler that logs messages to a file
    file_handler = RotatingFileHandler(log_file, maxBytes=1024*1024*5, backupCount=5)
    
    # Create a JSON formatter
    formatter = jsonlogger.JsonFormatter(
        '%(asctime)s %(name)s %(levelname)s %(message)s'
    )
    
    file_handler.setFormatter(formatter)
    logger.addHandler(file_handler)

    # Add a handler for Firestore logging if a client is provided
    if firestore_client:
        firestore_handler = FirestoreLogHandler(firestore_client)
        firestore_handler.setLevel(logging.INFO)
        logger.addHandler(firestore_handler)

    # Also add a handler for console output for local development
    console_handler = logging.StreamHandler()
    console_formatter = logging.Formatter(
        '%(asctime)s - %(name)s - %(levelname)s - %(message)s'
    )
    console_handler.setFormatter(console_formatter)
    logger.addHandler(console_handler)

    logging.info("Structured JSON logging configured.")

if __name__ == '__main__':
    setup_logging()
    logging.info("This is an info message.")
    logging.warning("This is a warning message.")
    logging.error("This is an error message.")
