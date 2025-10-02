import logging
import sys

def setup_logging():
    """
    Configures a centralized logger for the application.
    """
    logging.basicConfig(
        level=logging.INFO,
        format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
        stream=sys.stdout
    )
    # You can add file handlers or other handlers here if needed
    # For example, to log to a file:
    # file_handler = logging.FileHandler('agent.log')
    # file_handler.setFormatter(logging.Formatter('%(asctime)s - %(name)s - %(levelname)s - %(message)s'))
    # logging.getLogger().addHandler(file_handler)

    logging.info("Logging configured.")