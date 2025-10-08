import logging
import os
import json
from google.cloud import pubsub_v1
from services.firestore_client import FirestoreClient
from agents.creative_agent import CreativeAgent
from agents.qa_agent import QAAgent
from agents.editing_agent import EditingAgent
from agents.image_agent import ImageAgent
from agents.publishing_agent import PublishingAgent
from utils.firestore_logger import FirestoreLogger

class ContentOrchestrator:
    def __init__(self):
        self.firestore_client = FirestoreClient()
        self.logger = FirestoreLogger("content_orchestrator")
        # Initialize all agents
        self.creative_agent = CreativeAgent()
        self.qa_agent = QAAgent()
        self.editing_agent = EditingAgent()
        self.image_agent = ImageAgent()
        self.publishing_agent = PublishingAgent()

    def process_task(self, task_id, task_data):
        self.logger.log(task_id, "Orchestrator started processing task.")
        # ... (rest of the processing logic)

def listen_for_tasks():
    """Listens for messages on the Pub/Sub topic and triggers the content creation process."""
    project_id = os.getenv("GCP_PROJECT_ID")
    subscription_name = "content-creation-subscription"
    subscriber = pubsub_v1.SubscriberClient()
    subscription_path = subscriber.subscription_path(project_id, subscription_name)

    def callback(message):
        logging.info(f"Received message: {message.data}")
        if message.data.decode("utf-8") == "run":
            orchestrator = ContentOrchestrator()
            tasks = orchestrator.firestore_client.get_content_queue(status="Ready")
            for task in tasks:
                orchestrator.process_task(task['id'], task)
        message.ack()

    streaming_pull_future = subscriber.subscribe(subscription_path, callback=callback)
    logging.info(f"Listening for messages on {subscription_path}...")

    try:
        streaming_pull_future.result()
    except Exception as e:
        logging.error(f"Listening for messages on {subscription_path} threw an exception: {e}")
        streaming_pull_future.cancel()

if __name__ == "__main__":
    listen_for_tasks()
