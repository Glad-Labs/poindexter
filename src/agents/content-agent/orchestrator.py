import logging
import time
import os
from utils.logging_config import setup_logging
from config import config
from utils.data_models import BlogPost
from services.strapi_client import StrapiClient
from services.firestore_client import FirestoreClient
from services.llm_client import LLMClient
from services.pexels_client import PexelsClient
from services.gcs_client import GCSClient
from agents.research_agent import ResearchAgent
from agents.creative_agent import CreativeAgent
from agents.image_agent import ImageAgent
from agents.qa_agent import QAAgent
from agents.publishing_agent import PublishingAgent
from services.pubsub_client import PubSubClient
from utils.firestore_logger import FirestoreLogHandler
import threading


class Orchestrator:
    """
    The primary coordinator for the content generation pipeline.
    This class initializes all necessary clients and agents, and manages the
    end-to-end workflow from task retrieval to final publication.
    """

    def __init__(self):
        # Initialize Firestore client first, as it's needed for logging
        self.firestore_client = FirestoreClient()
        self._setup_logging()

        logging.info("=" * 80)
        logging.info("INITIALIZING NEW ORCHESTRATOR RUN")
        logging.info("=" * 80)
        self.config = config
        self.paused = False  # Add a paused state
        self._ensure_directories_exist()

        # Initialize other clients
        self.strapi_client = StrapiClient()
        self.llm_client = LLMClient()
        self.pexels_client = PexelsClient()
        self.gcs_client = GCSClient()

        # Initialize agents
        self.research_agent = ResearchAgent()
        self.creative_agent = CreativeAgent(self.llm_client)
        self.image_agent = ImageAgent(
            self.llm_client, self.pexels_client, self.gcs_client, self.strapi_client
        )
        self.qa_agent = QAAgent(self.llm_client)
        self.publishing_agent = PublishingAgent(self.strapi_client)

        # Initialize Pub/Sub client for command listening
        if config.GCP_PROJECT_ID and config.PUBSUB_TOPIC and config.PUBSUB_SUBSCRIPTION:
            self.pubsub_client = PubSubClient(
                project_id=config.GCP_PROJECT_ID,
                topic_id=config.PUBSUB_TOPIC,
                subscription_id=config.PUBSUB_SUBSCRIPTION,
                orchestrator=self,
            )
        else:
            self.pubsub_client = None
            logging.warning(
                "Pub/Sub client not initialized due to missing configuration."
            )

        logging.info("Orchestrator and all clients/agents initialized.")

    def start_pubsub_listener(self):
        """Starts the Pub/Sub listener in a separate thread if it exists."""
        if self.pubsub_client:
            listener_thread = threading.Thread(
                target=self.pubsub_client.listen_for_messages, daemon=True
            )
            listener_thread.start()

    def _setup_logging(self):
        """Initializes the logging configuration for the application."""
        setup_logging(self.firestore_client)

    def _ensure_directories_exist(self):
        try:
            image_path = self.config.IMAGE_STORAGE_PATH
            if image_path and not os.path.exists(image_path):
                os.makedirs(image_path)
                logging.info(f"Created required directory: {image_path}")
        except OSError as e:
            logging.error(
                f"Fatal: Could not create directory {self.config.IMAGE_STORAGE_PATH}. Error: {e}"
            )
            raise

    def run_batch_job(self):
        """
        Runs the content generation process in batch mode using Firestore as the task queue.
        """
        tasks = self.firestore_client.get_content_queue()
        if not tasks:
            logging.info("No new content tasks found in Firestore.")
            return

        published_posts_map = self.strapi_client.get_all_published_posts()

        for task in tasks:
            blog_post = BlogPost(
                topic=task["topic"],
                primary_keyword=task["primary_keyword"],
                target_audience=task["target_audience"],
                category=task["category"],
                task_id=task["id"],
            )
            self._process_post(blog_post, published_posts_map)

    def _process_post(self, post: BlogPost, published_posts_map: dict):
        """
        Manages the lifecycle of a single blog post from creation to publication.
        """
        post.published_posts_map = published_posts_map
        run_id = None
        firestore_handler = None

        try:
            # Set up Firestore logging for this specific run
            root_logger = logging.getLogger()
            firestore_handler = next(
                (h for h in root_logger.handlers if isinstance(h, FirestoreLogHandler)),
                None,
            )

            if post.task_id:
                run_id = self.firestore_client.log_run(post.task_id, post.topic)
                if firestore_handler:
                    firestore_handler.set_run_id(run_id)
                self.firestore_client.update_task_status(post.task_id, "In Progress")

            logging.info(
                f"Processing post: '{post.topic}' (Task ID: {post.task_id}, Run ID: {run_id})"
            )

            research_findings = self.research_agent.run(
                post.topic, post.primary_keyword.split(",")
            )
            post.research_data = research_findings
            if run_id:
                self.firestore_client.update_run(run_id, status="Research Complete")

            approved = False
            for i in range(post.refinement_loops):
                post = self.creative_agent.run(post, is_refinement=(i > 0))
                if run_id:
                    self.firestore_client.update_run(
                        run_id, status=f"Draft {i+1} Created"
                    )

                if post.raw_content:
                    approved, qa_feedback = self.qa_agent.run(post, post.raw_content)
                    post.qa_feedback.append(qa_feedback)
                    if run_id:
                        self.firestore_client.update_run(
                            run_id, status=f"QA Review {i+1} Complete"
                        )
                else:
                    approved = False
                    qa_feedback = "No content was generated by the creative agent."
                    post.qa_feedback.append(qa_feedback)
                    logging.warning("Skipping QA check as no content was generated.")

                if approved:
                    break

            if not approved:
                raise Exception(
                    "Failed to produce satisfactory content after refinement loops."
                )

            post = self.image_agent.run(post)
            if run_id:
                self.firestore_client.update_run(
                    run_id, status="Image Processing Complete"
                )

            post = self.publishing_agent.run(post)

            if not post.strapi_url:
                raise Exception("Publishing failed to return a valid Strapi URL.")

            logging.info(f"Successfully published post: {post.strapi_url}")
            if run_id:
                self.firestore_client.update_run(
                    run_id,
                    status="Published",
                    post_data={"strapi_url": post.strapi_url},
                )
            if post.task_id:
                self.firestore_client.update_task_status(
                    post.task_id, "Published", post.strapi_url
                )

        except Exception as e:
            error_message = f"An error occurred: {e}"
            logging.error(error_message, exc_info=True)
            if run_id:
                self.firestore_client.update_run(
                    run_id, status="Failed", post_data={"error": error_message}
                )
            if post.task_id:
                self.firestore_client.update_task_status(
                    post.task_id, "Error", error_message=error_message
                )
        finally:
            # Clear the run_id from the handler to stop logging to this run
            if firestore_handler:
                firestore_handler.set_run_id(None)


if __name__ == "__main__":
    orchestrator = Orchestrator()
    orchestrator.start_pubsub_listener()
    POLL_INTERVAL = 60  # seconds

    logging.info(
        f"Starting autonomous agent loop. Polling for new tasks every {POLL_INTERVAL} seconds."
    )
    try:
        while True:
            if not orchestrator.paused:
                orchestrator.run_batch_job()
            else:
                logging.info("Agent is paused. Skipping task processing.")
            time.sleep(POLL_INTERVAL)
    except KeyboardInterrupt:
        logging.info("Agent loop terminated by user.")
    except Exception as e:
        logging.critical(
            f"A fatal error occurred in the main agent loop: {e}", exc_info=True
        )
