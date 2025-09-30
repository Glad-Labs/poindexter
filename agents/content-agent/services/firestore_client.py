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
        Initializes the Firestore client.
        Uses the project ID from the central config.
        """
        self.db = firestore.Client(project=config.GCP_PROJECT_ID)
        print("Firestore client initialized.")

    def update_document(self, document_id: str, data: dict):
        """
        Creates or updates a document in the 'agent_runs' collection.
        """
        if not self.db:
            logging.warning("Firestore client not available. Skipping update.")
            return
        try:
            doc_ref = self.db.collection(self.collection_name).document(document_id)
            # Use set with merge=True to create or update fields without overwriting the whole doc
            doc_ref.set(data, merge=True)
            logging.debug(f"Updated Firestore document '{document_id}' in collection '{self.collection_name}'.")
        except Exception as e:
            logging.error(f"Failed to update Firestore document '{document_id}': {e}", exc_info=True)

    def create_task(self, task_name: str, agent_id: str, priority: int = 3) -> Optional[str]:
        """
        Creates a new task document in the 'tasks' collection.

        Args:
            task_name (str): A descriptive name for the task.
            agent_id (str): The ID of the agent assigned to this task.
            priority (int): The priority of the task (1-5).

        Returns:
            Optional[str]: The ID of the newly created task document, or None on failure.
        """
        try:
            tasks_ref = self.db.collection('tasks')
            task_data = {
                "agentId": agent_id,
                "taskName": task_name,
                "status": "queued",
                "createdAt": datetime.utcnow(),
                "updatedAt": datetime.utcnow(),
                "metadata": {
                    "priority": priority,
                    "relatedContentId": None
                }
            }
            # Add a new doc with an auto-generated ID
            update_time, task_ref = tasks_ref.add(task_data)
            print(f"Created new task with ID: {task_ref.id}")
            return task_ref.id
        except Exception as e:
            print(f"Error creating task in Firestore: {e}")
            return None

    def update_task_status(self, task_id: str, status: str):
        """
        Updates the status of a specific task.

        Args:
            task_id (str): The document ID of the task to update.
            status (str): The new status ('in_progress', 'completed', 'failed').
        """
        if not self.db:
            logging.warning("Firestore client not available. Skipping task status update.")
            return
        try:
            task_ref = self.db.collection('tasks').document(task_id)
            task_ref.update({
                "status": status,
                "updatedAt": datetime.utcnow()
            })
            logging.info(f"Updated task {task_id} status to: {status}")
        except Exception as e:
            logging.error(f"Failed to update status for task ID '{task_id}': {e}", exc_info=True)

# Example of how to use the client
if __name__ == '__main__':
    # This block is for testing purposes.
    # It now relies on the config object, which checks for the env var.
    fs_client = FirestoreClient()
    task_id = fs_client.create_task("Generate initial content brief", "creative-agent-v1")
    if task_id:
        fs_client.update_task_status(task_id, "in_progress")
        fs_client.update_task_status(task_id, "completed")
