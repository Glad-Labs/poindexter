import logging
from concurrent.futures import TimeoutError
from google.cloud import pubsub_v1
from config import config

logger = logging.getLogger(__name__)

class PubSubClient:
    """Client for interacting with Google Cloud Pub/Sub."""

    def __init__(self, orchestrator):
        self.publisher = pubsub_v1.PublisherClient()
        self.subscriber = pubsub_v1.SubscriberClient()
        self.orchestrator = orchestrator
        self.topic_path = f'projects/{config.GCP_PROJECT_ID}/topics/agent-commands'
        self.subscription_path = f'projects/{config.GCP_PROJECT_ID}/subscriptions/content-agent-subscription'

    def listen_for_messages(self):
        """Creates a subscription and enters a loop to listen for messages."""
        try:
            self.subscriber.create_subscription(name=self.subscription_path, topic=self.topic_path)
            logger.info(f"Subscription created: {self.subscription_path}")
        except Exception as e:
            logger.info(f"Subscription already exists or other error: {e}")

        streaming_pull_future = self.subscriber.subscribe(self.subscription_path, callback=self._message_callback)
        logger.info(f"Listening for messages on {self.subscription_path}...")

        try:
            # Keep the main thread alive to listen for messages.
            streaming_pull_future.result()
        except TimeoutError:
            streaming_pull_future.cancel()
            logger.info("Streaming pull future timed out.")
        except Exception as e:
            logger.error(f"An error occurred while listening for messages: {e}")
            streaming_pull_future.cancel()

    def _message_callback(self, message):
        """Callback function to handle incoming Pub/Sub messages."""
        try:
            logger.info(f"Received message: {message.data}")
            command = message.data.decode("utf-8")
            
            if command == "RUN_JOB":
                logger.info("'RUN_JOB' command received. Triggering orchestrator job.")
                self.orchestrator.run_job()
            else:
                logger.warning(f"Unknown command received: {command}")

            message.ack()
        except Exception as e:
            logger.error(f"Error processing message: {e}")
            message.nack()
