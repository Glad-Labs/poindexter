import os
import logging
from google.cloud import firestore
from datetime import datetime
from config import config
from typing import Optional, Any

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
        self.run_collection_name = "agent_runs"  # New collection for logging runs
        logging.info("Firestore client initialized.")

    def log_run(self, task_id: str, topic: str, status: str = "Starting") -> str:
        """
        Logs the start of a new agent run and returns the Firestore document ID.

        Args:
            task_id (str): The Firestore document ID of the task.
            topic (str): The topic of the blog post.
            status (str): The initial status of the run.

        Returns:
            str: The unique ID of the Firestore document for this run.
        """
        try:
            run_data = {
                "task_id": task_id,
                "topic": topic,
                "status": status,
                "startedAt": datetime.utcnow(),
                "updatedAt": datetime.utcnow(),
                "history": [{
                    "timestamp": datetime.utcnow(),
                    "status": "Run Started"
                }]
            }
            doc_ref = self.db.collection(self.run_collection_name).add(run_data)
            run_id = doc_ref[1].id
            logging.info(f"Started and logged new run with ID: {run_id}")
            return run_id
        except Exception as e:
            logging.error(f"Failed to log new agent run: {e}")
            raise

    def update_run(self, run_id: str, status: Optional[str] = None, post_data: Optional[dict] = None):
        """
        Updates the status and other details of an ongoing agent run.

        Args:
            run_id (str): The Firestore document ID of the run.
            status (str, optional): The new status to set.
            post_data (dict, optional): A dictionary of post-related data to merge.
        """
        if not run_id:
            logging.warning("Update_run called with no run_id. Skipping Firestore update.")
            return
            
        try:
            doc_ref = self.db.collection(self.run_collection_name).document(run_id)
            update_data: dict[str, Any] = {
                "updatedAt": datetime.utcnow()
            }
            if status:
                update_data["status"] = status
                # Add a history entry for the status change
                update_data["history"] = firestore.ArrayUnion([{
                    "timestamp": datetime.utcnow(),
                    "status": status
                }])

            if post_data:
                # Merge the post data into the document
                for key, value in post_data.items():
                    update_data[key] = value

            doc_ref.set(update_data, merge=True)
            logging.info(f"Updated Firestore run document '{run_id}' with status '{status}'.")
        except Exception as e:
            logging.error(f"Failed to update Firestore run document '{run_id}': {e}")

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

    def get_content_queue(self) -> list[dict]:
        """
        Fetches all tasks from the 'tasks' collection with the status 'Ready'.
        """
        try:
            tasks_ref = self.db.collection("tasks").where("status", "==", "Ready")
            docs = tasks_ref.stream()
            tasks = [{"id": doc.id, **doc.to_dict()} for doc in docs]
            logging.info(f"Found {len(tasks)} tasks in the content queue.")
            return tasks
        except Exception as e:
            logging.error(f"Failed to get content queue from Firestore: {e}")
            return []

    def update_task_status(self, task_id: str, status: str, url: Optional[str] = None):
        """
        Updates the status of a specific task in the 'tasks' collection.
        """
        try:
            task_ref = self.db.collection("tasks").document(task_id)
            update_data = {
                "status": status,
                "updatedAt": datetime.utcnow()
            }
            if url:
                update_data["url"] = url
            task_ref.update(update_data)
            logging.info(f"Updated task '{task_id}' to status '{status}'.")
        except Exception as e:
            logging.error(f"Failed to update task status for '{task_id}': {e}")
