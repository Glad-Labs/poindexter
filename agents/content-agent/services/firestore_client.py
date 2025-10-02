import os
import logging
from google.cloud import firestore
from datetime import datetime
from config import config
from typing import Optional

class FirestoreClient:
    """
    Client for interacting with Google Cloud Firestore to log real-time agent status.
    """
    def __init__(self):
        """
        Initializes the Firestore client using the project ID from the central config.
        """
        self.db = firestore.Client(project=config.GCP_PROJECT_ID)
        self.collection_name = config.FIRESTORE_COLLECTION
        logging.info("Firestore client initialized.")

    def update_document(self, document_id: str, data: dict):
        """
        Creates or updates a document in the configured Firestore collection.

        Args:
            document_id (str): The ID of the document to create or update.
            data (dict): A dictionary of fields to set or merge.
        """
        try:
            doc_ref = self.db.collection(self.collection_name).document(document_id)
            # Add an 'updatedAt' timestamp to every update for better tracking
            data_with_timestamp = data.copy()
            data_with_timestamp['updatedAt'] = datetime.utcnow()
            doc_ref.set(data_with_timestamp, merge=True)
            logging.info(f"Updated Firestore document '{document_id}'.")
        except Exception as e:
            logging.error(f"Failed to update Firestore document '{document_id}': {e}")

# Example of how to use the client
if __name__ == '__main__':
    # This block is for testing purposes.
    # It now relies on the config object, which checks for the env var.
    fs_client = FirestoreClient()
    task_id = fs_client.create_task("Generate initial content brief", "creative-agent-v1")
    if task_id:
        fs_client.update_task_status(task_id, "in_progress")
        fs_client.update_task_status(task_id, "completed")
