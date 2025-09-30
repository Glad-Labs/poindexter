import logging
import utils.logging_config
from typing import Dict
import os

# Import clients and data models
from services.google_sheets_client import GoogleSheetsClient
from services.strapi_client import StrapiClient
from services.firestore_client import FirestoreClient
from services.llm_client import LLMClient
from services.local_llm_client import LocalLLMClient # ADD THIS
from utils.data_models import BlogPost
from utils.helpers import slugify

# Import CrewAI components
from crewai import Task, Crew, Process

# Import Agents for their logic
from agents.creative_agent import create_creative_agent
from agents.image_agent import ImageAgent
from agents.editing_agent import create_editing_agent
from agents.qa_agent import QAAgent
from agents.publishing_agent import create_publishing_agent, create_strapi_draft

# Import the new tools and shared state
from agents.tools import SharedState, ContentCreationTool, ImageProcessingTool, QAReviewTool, EditingTool
from utils.tools import StrapiPublishTool

from config import config # Import the centralized config

# Initialize the Firestore client for transparent logging
# The client now automatically uses the GCP_PROJECT_ID from the config
firestore_client = FirestoreClient()

# Initialize clients and tools
strapi_tool = StrapiPublishTool()

# Create agents and pass the tool to the publisher
creative_agent = create_creative_agent()
editing_agent = create_editing_agent()
publishing_agent = create_publishing_agent(strapi_tool)

class Orchestrator:
    """
    Coordinates the content creation pipeline using CrewAI.
    """
    def __init__(self):
        logging.info("Orchestrator initializing...")
        self.sheets_client = GoogleSheetsClient()
        self.strapi_client = StrapiClient()
        self.firestore_client = FirestoreClient()
        self.llm_client = LLMClient(credentials=self.sheets_client.creds)
        self.local_llm_client = LocalLLMClient() # ADD THIS
        self.is_processing = False
        logging.info("Orchestrator and clients initialized.")

    def run_job(self):
        if self.is_processing:
            logging.info("Skipping run, a job is already in progress.")
            return
        
        self.is_processing = True
        try:
            logging.info("Checking for new content tasks...")
            # Get the map of published posts for internal linking
            published_posts_map = self.sheets_client.get_published_posts_map()
            
            tasks = self.sheets_client.get_new_content_requests()
            if tasks:
                for task_data in tasks:
                    self.process_single_post(task_data, published_posts_map)
            else:
                logging.info("No new topics found.")
        finally:
            self.is_processing = False

    def process_single_post(self, post_data: Dict, published_posts_map: Dict[str, str]):
        """
        Manages the end-to-end agent pipeline for a single blog post using CrewAI.
        """
        post = BlogPost(**post_data)
        post.published_posts_map = published_posts_map # Assign the map here
        post.slug = slugify(post.topic) # Generate slug needed for Strapi
        doc_id = f"post_{post.sheet_row_index}"
        self.firestore_client.update_document(doc_id, {"status": "Processing", "topic": post.topic})
        self.sheets_client.update_status_by_row(post.sheet_row_index, "Processing")

        try:
            # 1. Initialize Agent Logic and Shared State
            shared_state = SharedState(post)
            # Define a persona for the creative agent
            creative_persona = "A witty and engaging tech blogger who simplifies complex topics."
            creative_agent_logic = CreativeAgent(
                llm_client=self.llm_client, 
                local_llm_client=self.local_llm_client, 
                persona=creative_persona
            )
            image_agent_logic = ImageAgent()
            qa_agent_logic = QAAgent(llm_client=self.llm_client)
            editing_agent_logic = EditingAgent()
            publishing_agent_logic = create_publishing_agent()  # Initialize the publishing agent logic

            # 2. Create Tools that wrap the agent logic
            content_tool = ContentCreationTool(creative_agent=creative_agent_logic, shared_state=shared_state)
            image_tool = ImageProcessingTool(image_agent=image_agent_logic, shared_state=shared_state)
            qa_tool = QAReviewTool(qa_agent=qa_agent_logic, shared_state=shared_state)
            editing_tool = EditingTool(editing_agent=editing_agent_logic, shared_state=shared_state)

            # 3. Define CrewAI Agents
            writer_agent = Agent(
                role='Expert Content Writer',
                goal=f'Create an engaging, well-structured, and SEO-optimized blog post about "{post.topic}".',
                backstory='You are a world-class content creator.',
                tools=[content_tool],
                verbose=True
            )

            visuals_agent = Agent(
                role='Visual Asset Manager',
                goal='Generate or find relevant images, upload them, and integrate them into the blog post.',
                backstory='You have a keen eye for visuals that enhance storytelling.',
                tools=[image_tool],
                verbose=True
            )

            qa_agent = Agent(
                role='Meticulous Quality Assurance Editor',
                goal='Critically review the blog post and its images for quality, relevance, and accuracy.',
                backstory='You are the guardian of quality. You must reject anything that is not excellent.',
                tools=[qa_tool],
                verbose=True
            )

            editor_agent = Agent(
                role='Final Editor',
                goal='Perform a final editing pass on the content to ensure it is polished and ready for publication.',
                backstory='You are a detail-oriented editor with an eye for perfection.',
                tools=[editing_tool],
                verbose=True
            )

            publishing_agent = Agent(
                role='Publishing Specialist',
                goal='Draft and publish the blog post to the Strapi CMS.',
                backstory='You are an expert in CMS operations and ensure smooth publishing.',
                tools=[create_strapi_draft],
                verbose=True
            )

            # 4. Define CrewAI Tasks
            task_create = Task(
                description=f"Create the initial content for the blog post on '{post.topic}'.",
                agent=writer_agent,
                expected_output="A confirmation that the content draft and metadata have been generated."
            )

            task_images = Task(
                description="Process all images for the blog post, upload them, and embed their URLs.",
                agent=visuals_agent,
                context=[task_create],
                expected_output="A confirmation that images are processed and embedded."
            )

            task_qa = Task(
                description="Perform a thorough quality review of the generated content and images.",
                agent=qa_agent,
                context=[task_images],
                expected_output="A final approval or a failure message."
            )

            task_edit = Task(
                description="Apply final edits to the approved blog post content.",
                agent=editor_agent,
                context=[task_qa],
                expected_output="A confirmation that the final edits are complete."
            )

            task_publish = Task(
                description="Draft and publish the blog post to the Strapi CMS.",
                agent=publishing_agent,
                context=[task_edit],
                expected_output="A confirmation that the post has been published to Strapi."
            )

            # 5. Assemble and Run the Crew
            content_crew = Crew(
                agents=[writer_agent, visuals_agent, qa_agent, editor_agent, publishing_agent],
                tasks=[task_create, task_images, task_qa, task_edit, task_publish],
                process=Process.sequential,
                verbose=True # Set to True or False, not a number
            )

            crew_result = content_crew.kickoff(inputs={'topic': post.topic})
            logging.info(f"Crew finished with result: {crew_result}")

            # 6. Final Publishing Step
            final_post = shared_state.post # Get the final, updated post object
            self.firestore_client.update_document(doc_id, {"status": "Publishing to Strapi"})
            self.sheets_client.update_status_by_row(final_post.sheet_row_index, "Publishing")
            
            post_id, post_url = self.strapi_client.publish_post(final_post)
            final_post.strapi_post_id = post_id
            final_post.strapi_url = post_url

            final_status = "Published to Strapi"
            self.sheets_client.update_status_by_row(final_post.sheet_row_index, final_status, final_post.strapi_url)
            self.firestore_client.update_document(doc_id, {"status": final_status, "strapi_url": final_post.strapi_url})
            logging.info(f"Successfully published post '{final_post.generated_title}' to Strapi.")

        except Exception as e:
            logging.error(f"Failed to process post for topic '{post.topic}'. Error: {e}", exc_info=True)
            self.sheets_client.update_status_by_row(post.sheet_row_index, "Error", str(e))
            self.firestore_client.update_document(doc_id, {"status": "Error", "error_message": str(e)})

def run_crew():
    """
    Executes the full content creation and publishing crew.
    """
    task_id = None
    try:
        print("Initializing full content workflow...")
        task_id = firestore_client.create_task(
            task_name="Generate, Refine, and Publish Blog Post",
            agent_id="content-creation-crew-v3"
        )

        if task_id:
            firestore_client.update_task_status(task_id, "in_progress")

            print("Executing Crew...")
            result = content_crew.kickoff()

            firestore_client.update_task_status(task_id, "completed")

            print("\n--- Workflow Completed ---")
            print("Final Result:")
            print(result)
            print("--------------------------")
        else:
            print("Failed to create a task in Firestore. Aborting workflow.")

    except Exception as e:
        print(f"\nAn error occurred during the workflow: {e}")
        if task_id:
            firestore_client.update_task_status(task_id, "failed")

def main():
    """Main function to run the orchestrator."""
    orchestrator = Orchestrator()
    logging.info("Starting orchestrator job...")
    orchestrator.run_job()

if __name__ == '__main__':
    main()
    # The config object now handles the check for the project ID at startup.
    # If the ID is missing, the program will have already raised an error.
    run_crew()