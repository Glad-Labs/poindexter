# orchestrator.py

import os
import logging
from crewai import Crew, Process
from config import config
from utils.logging_config import setup_logging
from utils.data_models import BlogPost
from services.google_sheets_client import GoogleSheetsClient
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

# --- Constants ---
# Defines the maximum number of times the content can be passed back to the
# creative agent for refinement based on QA feedback. This prevents infinite loops.
MAX_REFINEMENT_LOOPS = 3

class Orchestrator:
    """
    The main orchestrator for the content creation pipeline.

    This class initializes all necessary clients and agents, and manages the
    end-to-end workflow of creating and publishing a blog post. It is designed
    to be triggered by an external event, such as a message from a Pub/Sub topic.

    Future Enhancement:
    - Refactor this class to be more lightweight. The main application entry point
      (e.g., a new `main.py` running in a Flask/FastAPI server) should handle
      incoming web requests (from Pub/Sub push subscriptions) and then delegate
      the core processing task to this Orchestrator.
    - Implement dependency injection for clients to make testing easier.
    """
    def __init__(self):
        """
        Initializes all clients, agents, and required directories.
        This setup is done once when the Orchestrator instance is created.
        """
        self.config = config
        self._setup_logging()
        self._ensure_directories_exist()

        logging.info("Orchestrator initializing...")
        # Initialize clients for external service communication
        self.sheets_client = GoogleSheetsClient()
        self.strapi_client = StrapiClient()
        self.firestore_client = FirestoreClient()
        self.llm_client = LLMClient()
        self.pexels_client = PexelsClient()
        self.gcs_client = GCSClient()

        # Initialize the specialized agents
        self.research_agent = ResearchAgent()
        self.creative_agent = CreativeAgent(self.llm_client)
        self.image_agent = ImageAgent(self.llm_client, self.pexels_client, self.gcs_client, self.strapi_client)
        self.qa_agent = QAAgent(self.llm_client)
        self.publishing_agent = PublishingAgent(self.strapi_client)
        
        logging.info("Orchestrator, clients, and agents initialized successfully.")

    def _setup_logging(self):
        """Initializes the structured logging configuration for the application."""
        setup_logging()

    def _ensure_directories_exist(self):
        """
        Checks for the existence of required local directories (e.g., for image
        storage) and creates them if they are missing. This prevents runtime errors.
        """
        try:
            image_path = self.config.IMAGE_STORAGE_PATH
            if not os.path.exists(image_path):
                os.makedirs(image_path)
                logging.info(f"Created required directory: {image_path}")
        except OSError as e:
            logging.error(f"Fatal: Could not create directory {self.config.IMAGE_STORAGE_PATH}. Error: {e}")
            raise

    def run_single_job(self, sheet_row_index: int):
        """
        Processes a single content task based on its row index in Google Sheets.

        Future Enhancement:
        - This method should be deprecated and replaced with a new method like
          `process_task(task_payload: dict)`.
        - The new method will receive task details (topic, keywords, etc.) directly
          from the Pub/Sub message payload, completely decoupling the core logic
          from Google Sheets as a task source.
        """
        logging.info(f"Orchestrator: Received legacy request to process job from sheet row {sheet_row_index}.")
        
        # Fetch all published posts to provide context to agents and avoid duplication.
        published_posts_map = self.sheets_client.get_all_published_posts()
        
        # Fetch all tasks marked as 'Ready'.
        # In a Pub/Sub model, this fetch would be replaced by parsing the message payload.
        tasks = self.sheets_client.get_content_queue()
        
        # Find the specific task corresponding to the triggered row index.
        post_to_process = next((post for post in tasks if post.sheet_row_index == sheet_row_index), None)

        if not post_to_process:
            logging.warning(f"No 'Ready' task found for row index {sheet_row_index}. The job may have been processed already or the status is incorrect. Skipping.")
            return

        self._process_post(post_to_process, published_posts_map)

    def run_job(self):
        """
        Main batch-processing loop that fetches all 'Ready' tasks and processes them.

        DEPRECATED: This method is part of the old cron-based architecture.
        The new architecture should be event-driven, with each task processed
        individually via `run_single_job` or its successor. This method should be
        removed once the transition to Pub/Sub is complete.
        """
        logging.warning("Orchestrator: Running deprecated `run_job` method. This should be replaced by event-driven triggers.")
        
        published_posts_map = self.sheets_client.get_all_published_posts()
        tasks = self.sheets_client.get_content_queue()

        if not tasks:
            logging.info("No new content tasks found in batch mode.")
            return

        for post in tasks:
            self._process_post(post, published_posts_map)

    def _process_post(self, post: BlogPost, published_posts_map: dict):
        """
        The core logic for processing a single blog post using a crew of AI agents.

        This method encapsulates the entire content creation lifecycle, from initial
        research to final publication, including iterative refinement.

        Future Enhancement:
        - Fully integrate CrewAI. Instead of calling agents sequentially, define a
          `Crew` with the agents and a set of `Tasks`. Then, kick off the crew
          with `crew.kickoff()` and let it manage the execution flow. This will
          make the process more robust and allow for more complex agent interactions.
        """
        post.published_posts_map = published_posts_map
        run_id = None  # Initialize run_id to ensure it's always available for error logging
        
        try:
            # Log the start of the process to Firestore for real-time monitoring.
            run_id = self.firestore_client.log_run(post.sheet_row_index, post.topic)
            self.sheets_client.update_sheet_status(post.sheet_row_index, "In Progress")
            logging.info(f"Processing post: '{post.topic}' (Row: {post.sheet_row_index}, Run ID: {run_id})")

            # === Agent Pipeline ===
            
            # 1. Research Agent
            logging.info("Executing Research Agent...")
            research_findings = self.research_agent.run(post.topic, post.primary_keyword.split(','))
            post.research_data = research_findings
            self.firestore_client.update_run(run_id, status="Research Complete", post_data={"research_data_summary": research_findings[:200]})

            # 2. Creative Agent & QA Loop
            approved = False
            qa_feedback = ""
            for i in range(post.refinement_loops):
                logging.info(f"Executing Creative Agent (Refinement loop {i+1}/{post.refinement_loops})...")
                
                # The creative agent uses the research data and any previous feedback.
                post = self.creative_agent.run(post, is_refinement=(i > 0))
                self.firestore_client.update_run(run_id, status=f"Draft {i+1} Created")

                logging.info("Executing QA Agent...")
                if not post.raw_content:
                    raise ValueError("Content from Creative Agent is empty. Cannot proceed with QA.")
                
                approved, qa_feedback = self.qa_agent.run(post, post.raw_content)
                post.qa_feedback.append(qa_feedback)
                self.firestore_client.update_run(run_id, status=f"QA Review {i+1} Complete", post_data={"qa_feedback": qa_feedback})

                if approved:
                    logging.info("QA Agent approved the content. Exiting refinement loop.")
                    break
                else:
                    logging.warning(f"QA Agent requested revisions: {qa_feedback}")
            
            if not approved:
                raise Exception(f"Failed to produce satisfactory content after {post.refinement_loops} refinement loops. Last feedback: {qa_feedback}")

            # 3. Image Agent
            logging.info("Executing Image Agent...")
            post = self.image_agent.run(post)
            self.firestore_client.update_run(run_id, status="Image Processing Complete")

            # 4. Publishing Agent
            logging.info("Executing Publishing Agent...")
            post = self.publishing_agent.run(post)
            
            # Final success logging
            logging.info(f"Successfully processed and published post: {post.strapi_url}")
            self.firestore_client.update_run(run_id, status="Published", post_data={"strapi_url": post.strapi_url})
            self.sheets_client.update_sheet_status(post.sheet_row_index, "Published", post.strapi_url or "")

        except Exception as e:
            # Centralized error handling
            error_message = f"An error occurred while processing post '{post.topic}': {e}"
            logging.error(error_message, exc_info=True)
            if run_id:
                self.firestore_client.update_run(run_id, status="Failed", post_data={"error": error_message})
            # Truncate error for sheet to avoid cell overflow
            self.sheets_client.update_sheet_status(post.sheet_row_index, "Error", error_message[:500])

# --- Main Execution Block ---
if __name__ == "__main__":
    """
    This block allows the script to be run directly for testing or for the
    legacy cron-based execution model.
    """
    # This is the entry point for the legacy cron job execution.
    # It should be phased out in favor of a web server entry point.
    orchestrator = Orchestrator()
    orchestrator.run_job()