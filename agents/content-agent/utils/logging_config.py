import logging
import logging.handlers
import os
import sys
from config import config

def setup_logging():
    """
    Configures a centralized, professional logging system with rotating file handlers.
    """
    # Ensure the log directory exists
    os.makedirs(config.LOG_DIR, exist_ok=True)

    # --- Root Logger for General Application Flow ---
    root_logger = logging.getLogger()
    root_logger.setLevel(logging.INFO)
    
    # Create a rotating file handler for the main app log
    app_handler = logging.handlers.RotatingFileHandler(
        config.APP_LOG_FILE,
        maxBytes=config.MAX_LOG_SIZE_MB * 1024 * 1024,
        backupCount=config.MAX_LOG_BACKUP_COUNT
    )
    app_formatter = logging.Formatter('%(asctime)s - %(name)s - %(levelname)s - %(message)s')
    app_handler.setFormatter(app_formatter)
    
    # Create a console handler for real-time output
    console_handler = logging.StreamHandler(sys.stdout)
    console_handler.setFormatter(app_formatter)

    root_logger.addHandler(app_handler)
    root_logger.addHandler(console_handler)

    # --- Dedicated Logger for LLM Prompts and Responses ---
    prompts_logger = logging.getLogger('prompts')
    prompts_logger.setLevel(logging.DEBUG)
    prompts_logger.propagate = False # Prevent prompts from appearing in the main app log
    
    # Create a rotating file handler for the prompts log
    prompts_handler = logging.handlers.RotatingFileHandler(
        config.PROMPTS_LOG_FILE,
        maxBytes=config.MAX_LOG_SIZE_MB * 1024 * 1024,
        backupCount=config.MAX_LOG_BACKUP_COUNT
    )
    prompts_formatter = logging.Formatter('%(asctime)s - %(message)s')
    prompts_handler.setFormatter(prompts_formatter)
    prompts_logger.addHandler(prompts_handler)

    logging.info("Professional logging system configured.")