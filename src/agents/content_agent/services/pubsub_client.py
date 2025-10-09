import os
import json
import logging
from concurrent.futures import TimeoutError
from google.cloud import pubsub_v1


class PubSubClient:
    def __init__(
        self, project_id: str, topic_id: str, subscription_id: str, orchestrator
    ):
        self.project_id = project_id
        self.topic_id = topic_id
        self.subscription_id = subscription_id
        self.orchestrator = orchestrator
        self.publisher = pubsub_v1.PublisherClient()
        self.subscriber = pubsub_v1.SubscriberClient()
        self.topic_path = self.publisher.topic_path(project_id, topic_id)
        self.subscription_path = self.subscriber.subscription_path(
            project_id, subscription_id
        )
        self._ensure_topic_and_subscription()

    def _ensure_topic_and_subscription(self):
        """Ensures that the Pub/Sub topic and subscription exist."""
        try:
            self.publisher.create_topic(request={"name": self.topic_path})
            logging.info(f"Topic {self.topic_path} created.")
        except Exception:
            logging.info(f"Topic {self.topic_path} already exists.")

        try:
            self.subscriber.create_subscription(
                request={"name": self.subscription_path, "topic": self.topic_path}
            )
            logging.info(f"Subscription {self.subscription_path} created.")
        except Exception:
            logging.info(f"Subscription {self.subscription_path} already exists.")

    def listen_for_messages(self):
        """Starts a background thread to listen for Pub/Sub messages."""
        streaming_pull_future = self.subscriber.subscribe(
            self.subscription_path, callback=self.message_callback
        )
        logging.info(f"Listening for messages on {self.subscription_path}...")

        with self.subscriber:
            try:
                streaming_pull_future.result()
            except TimeoutError:
                streaming_pull_future.cancel()
                logging.warning("Listening for messages timed out.")
            except Exception as e:
                logging.error(
                    f"An error occurred while listening for messages: {e}",
                    exc_info=True,
                )

    def message_callback(self, message):
        """Handles incoming Pub/Sub messages."""
        try:
            data = json.loads(message.data)
            command = data.get("command")
            logging.info(f"Received message: {command}")

            if command == "PAUSE_AGENT":
                self.orchestrator.paused = True
                logging.warning("PAUSE command received. Pausing agent.")
            elif command == "RESUME_AGENT":
                self.orchestrator.paused = False
                logging.info("RESUME command received. Resuming agent.")
            else:
                logging.warning(f"Unknown command received: {command}")

            message.ack()
        except json.JSONDecodeError:
            logging.error(f"Received a non-JSON message: {message.data}")
            message.ack()
        except Exception as e:
            logging.error(f"Error processing message: {e}", exc_info=True)
            message.nack()
