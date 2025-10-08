import logging
from google.cloud import pubsub_v1
import os

class PubSubClient:
    """
    A client for interacting with Google Cloud Pub/Sub.
    """
    def __init__(self):
        """Initializes the PubSubClient."""
        self.project_id = os.getenv("GCP_PROJECT_ID")
        self.publisher = pubsub_v1.PublisherClient()
        logging.info("Pub/Sub Client initialized.")

    def publish_message(self, topic_id: str, message: str):
        """
        Publishes a message to a Pub/Sub topic.
        """
        try:
            topic_path = self.publisher.topic_path(self.project_id, topic_id)
            future = self.publisher.publish(topic_path, message.encode("utf-8"))
            future.result()  # Wait for the publish to complete
            logging.info(f"Published message to topic {topic_id}.")
        except Exception as e:
            logging.error(f"Error publishing message to topic {topic_id}: {e}", exc_info=True)
            raise
