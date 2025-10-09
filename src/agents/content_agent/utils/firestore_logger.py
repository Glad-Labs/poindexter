import logging
from services.firestore_client import FirestoreClient
from typing import Optional


class FirestoreLogHandler(logging.Handler):
    """
    A custom logging handler that sends log records to Firestore.
    """

    def __init__(self, firestore_client: FirestoreClient):
        super().__init__()
        self.firestore_client = firestore_client
        self.run_id = None

    def set_run_id(self, run_id: Optional[str]):
        """Sets the current run ID for logging."""
        self.run_id = run_id

    def emit(self, record):
        """
        Sends a log record to Firestore if a run ID is set.
        """
        if self.run_id:
            log_entry = self.format(record)
            self.firestore_client.add_log_to_run(
                self.run_id, record.levelname, log_entry
            )
