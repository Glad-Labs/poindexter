import os
import logging
import argparse
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

MAX_REFINEMENT_LOOPS = 3

class Orchestrator:
    def __init__(self):
        self.config = config
        self._setup_logging()
        self._ensure_directories_exist()
        logging.info("Orchestrator initialized.")

    def _setup_logging(self):
        setup_logging()

    def _ensure_directories_exist(self):
        try:
            image_path = self.config.IMAGE_STORAGE_PATH
            if not os.path.exists(image_path):
                os.makedirs(image_path)
                logging.info(f"Created required directory: {image_path}")
        except OSError as e:
            logging.error(f"Fatal: Could not create directory {self.config.IMAGE_STORAGE_PATH}. Error: {e}")
            raise

    def run_single_job(self, sheet_row_index: int):
        logging.info(f"Orchestrator: Received legacy request to process job from sheet row {sheet_row_index}.")
        sheets_client = GoogleSheetsClient()
        published_posts_map = sheets_client.get_all_published_posts()
        tasks = sheets_client.get_content_queue()
        post_to_process = next((post for post in tasks if post.sheet_row_index == sheet_row_index), None)
        if not post_to_process:
            logging.warning(f"No 'Ready' task found for row index {sheet_row_index}. Skipping.")
            return
        self._process_post(post_to_process, published_posts_map)

    def run_job(self):
        logging.warning("Orchestrator: Running deprecated `run_job` method.")
        sheets_client = GoogleSheetsClient()
        published_posts_map = sheets_client.get_all_published_posts()
        tasks = sheets_client.get_content_queue()
        if not tasks:
            logging.info("No new content tasks found in batch mode.")
            return
        for post in tasks:
            self._process_post(post, published_posts_map)

    def _process_post(self, post: BlogPost, published_posts_map: dict):
        strapi_client = StrapiClient()
        firestore_client = FirestoreClient()
        llm_client = LLMClient()
        pexels_client = PexelsClient()
        gcs_client = GCSClient()
        sheets_client = GoogleSheetsClient()
        research_agent = ResearchAgent()
        creative_agent = CreativeAgent(llm_client)
        image_agent = ImageAgent(llm_client, pexels_client, gcs_client, strapi_client)
        qa_agent = QAAgent(llm_client)
        publishing_agent = PublishingAgent(strapi_client)
        post.published_posts_map = published_posts_map
        run_id = None
        try:
            run_id = firestore_client.log_run(post.sheet_row_index, post.topic)
            sheets_client.update_sheet_status(post.sheet_row_index, "In Progress")
            logging.info(f"Processing post: '{post.topic}' (Row: {post.sheet_row_index}, Run ID: {run_id})")
            logging.info("Executing Research Agent...")
            research_findings = research_agent.run(post.topic, post.primary_keyword.split(','))
            post.research_data = research_findings
            firestore_client.update_run(run_id, status="Research Complete", post_data={"research_data_summary": research_findings[:200]})
            approved = False
            qa_feedback = ""
            for i in range(post.refinement_loops):
                logging.info(f"Executing Creative Agent (Refinement loop {i+1}/{post.refinement_loops})...")
                post = creative_agent.run(post, is_refinement=(i > 0))
                firestore_client.update_run(run_id, status=f"Draft {i+1} Created")
                logging.info("Executing QA Agent...")
                if not post.raw_content:
                    raise ValueError("Content from Creative Agent is empty. Cannot proceed with QA.")
                approved, qa_feedback = qa_agent.run(post, post.raw_content)
                post.qa_feedback.append(qa_feedback)
                firestore_client.update_run(run_id, status=f"QA Review {i+1} Complete", post_data={"qa_feedback": qa_feedback})
                if approved:
                    logging.info("QA Agent approved the content. Exiting refinement loop.")
                    break
                else:
                    logging.warning(f"QA Agent requested revisions: {qa_feedback}")
            if not approved:
                raise Exception(f"Failed to produce satisfactory content after {post.refinement_loops} refinement loops. Last feedback: {qa_feedback}")
            logging.info("Executing Image Agent...")
            post = image_agent.run(post)
            firestore_client.update_run(run_id, status="Image Processing Complete")
            logging.info("Executing Publishing Agent...")
            post = publishing_agent.run(post)
            logging.info(f"Successfully processed and published post: {post.strapi_url}")
            firestore_client.update_run(run_id, status="Published", post_data={"strapi_url": post.strapi_url})
            sheets_client.update_sheet_status(post.sheet_row_index, "Published", post.strapi_url or "")
        except Exception as e:
            error_message = f"An error occurred while processing post '{post.topic}': {e}"
            logging.error(error_message, exc_info=True)
            if run_id:
                firestore_client.update_run(run_id, status="Failed", post_data={"error": error_message})
            sheets_client.update_sheet_status(post.sheet_row_index, "Error", error_message[:500])

    def generate_topic_ideas(self, trends: list[str]):
        """
        Generates blog post ideas based on a given list of trends.
        """
        logging.info("Generating topic ideas...")
        if not trends:
            logging.warning("No trends provided, cannot generate topic ideas.")
            return
        blog_ideas = []
        for trend in trends:
            if trend:
                blog_ideas.append(f"Exploring the Impact of {trend} on the Future of Tech")
                blog_ideas.append(f"A Deep Dive into {trend}: What You Need to Know")
        if blog_ideas:
            logging.info("Generated Blog Post Ideas:")
            for idea in list(set(blog_ideas))[:5]:
                print(f"- {idea}")
        else:
            logging.warning("Could not generate any blog post ideas.")

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Content creation orchestrator.")
    parser.add_argument("--trends", nargs='+', help="List of trends to generate blog post ideas from.")
    args = parser.parse_args()
    orchestrator = Orchestrator()
    if args.trends:
        orchestrator.generate_topic_ideas(args.trends)
    else:
        orchestrator.run_job()