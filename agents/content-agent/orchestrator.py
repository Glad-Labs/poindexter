import logging
from utils.logging_config import setup_logging
from services.google_sheets_client import GoogleSheetsClient
from services.strapi_client import StrapiClient
from services.firestore_client import FirestoreClient
from services.llm_client import LLMClient
from services.pexels_client import PexelsClient
from services.gcs_client import GCSClient
from agents.research_agent import ResearchAgent # Import the ResearchAgent
from agents.creative_agent import CreativeAgent
from agents.image_agent import ImageAgent
from agents.qa_agent import QAAgent # Import the QAAgent
from agents.publishing_agent import PublishingAgent

MAX_REFINEMENT_LOOPS = 3

class Orchestrator:
    """
    Coordinates the advanced content creation pipeline, including a multi-stage
    QA and refinement loop.
    """
    def __init__(self):
        setup_logging()
        logging.info("Orchestrator initializing...")
        
        # Initialize all clients
        self.sheets_client = GoogleSheetsClient()
        self.strapi_client = StrapiClient()
        self.firestore_client = FirestoreClient()
        self.llm_client = LLMClient()
        self.pexels_client = PexelsClient()
        self.gcs_client = GCSClient()

        # Initialize all agents
        self.research_agent = ResearchAgent()
        self.creative_agent = CreativeAgent(self.llm_client)
        self.image_agent = ImageAgent(self.llm_client, self.pexels_client, self.gcs_client, self.strapi_client)
        self.qa_agent = QAAgent(self.llm_client)
        self.publishing_agent = PublishingAgent(self.strapi_client)
        
        logging.info("Orchestrator and clients initialized.")

    def run_job(self):
        logging.info("Orchestrator: Checking for new content tasks...")
        
        published_posts_map = self.sheets_client.get_published_posts_map()
        tasks = self.sheets_client.get_new_content_requests()

        if not tasks:
            logging.info("No new content tasks found.")
            return

        for post in tasks:
            post.published_posts_map = published_posts_map
            doc_id = f"post_{post.sheet_row_index}"
            
            try:
                logging.info(f"--- Starting processing for topic: {post.topic} ---")
                self.firestore_client.update_task_status(doc_id, "Processing")
                self.sheets_client.update_status_by_row(post.sheet_row_index, "Processing")

                # --- Stage 1: Research ---
                research_context = self.research_agent.run(post.topic)

                # --- Stage 2: Content Generation & Refinement Loop ---
                approved = False
                for i in range(MAX_REFINEMENT_LOOPS):
                    logging.info(f"Content Generation Attempt {i+1}/{MAX_REFINEMENT_LOOPS}")
                    post = self.creative_agent.run(post, research_context=research_context, is_refinement=(i > 0))
                    
                    approved, feedback = self.qa_agent.review_content(post)
                    if approved:
                        logging.info("Content draft approved by QA.")
                        break
                    else:
                        logging.warning(f"Content draft rejected by QA. Feedback: {feedback}")
                        if i == MAX_REFINEMENT_LOOPS - 1:
                            raise Exception("Content failed QA review after maximum refinement attempts.")

                # --- Stage 3: Image Processing ---
                post = self.image_agent.run(post)

                # --- Stage 4: Final QA Review ---
                approved, feedback = self.qa_agent.review_content(post)
                if not approved:
                    raise Exception(f"Final post with images failed QA review. Feedback: {feedback}")

                # --- Stage 5: Publishing ---
                post = self.publishing_agent.run(post)

                # Log final success
                final_status = "Published to Strapi" if post.status != "Error" else "Error"
                final_url = post.strapi_url if post.strapi_url else ""
                self.sheets_client.update_status_by_row(post.sheet_row_index, final_status, final_url)
                self.firestore_client.update_document(doc_id, {"status": final_status, "strapi_url": final_url})
                logging.info(f"--- Successfully processed post: {post.generated_title} ---")

            except Exception as e:
                logging.error(f"A critical error occurred while processing topic '{post.topic}'. Error: {e}", exc_info=True)
                self.sheets_client.update_status_by_row(post.sheet_row_index, "Error", str(e))
                self.firestore_client.update_task_status(doc_id, "Error")

def main():
    """Main function to run the orchestrator."""
    orchestrator = Orchestrator()
    orchestrator.run_job()

if __name__ == "__main__":
    main()