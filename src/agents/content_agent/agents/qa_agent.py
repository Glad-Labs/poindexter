import logging
import json
<<<<<<< HEAD
from src.agents.content_agent.config import config
from src.agents.content_agent.services.llm_client import LLMClient
from src.agents.content_agent.utils.data_models import BlogPost
from src.agents.content_agent.utils.helpers import load_prompts_from_file
from src.agents.content_agent.utils.tools import CrewAIToolsFactory
=======
from ..config import config
from ..services.llm_client import LLMClient
from ..utils.data_models import BlogPost
from ..utils.helpers import load_prompts_from_file
>>>>>>> feat/refine

logger = logging.getLogger(__name__)


class QAAgent:
    def __init__(self, llm_client: LLMClient):
        logger.info("Initializing QAAgent (v2 - Fixed draft key)")
        self.llm_client = llm_client
        self.prompts = load_prompts_from_file(config.PROMPTS_PATH)
        self.tools = CrewAIToolsFactory.get_content_agent_tools()

    async def run(self, post: BlogPost, previous_content: str) -> tuple[bool, str]:
        """
        Reviews the content against the quality rubric. This method is designed
        to be called within the iterative refinement loop of the orchestrator.

        Args:
            post (BlogPost): The blog post object containing all context.
            previous_content (str): The specific version of the content to be reviewed.
                                    In the future, this could be used to compare versions,
                                    but for now, it's the current draft.

        Returns:
            tuple[bool, str]: A tuple containing the approval status (boolean)
                              and the feedback (string).
        """
        logger.info(f"QAAgent: Reviewing content for '{post.topic}'.")

        # Debug logging for troubleshooting
        logger.debug(f"QAAgent DEBUG: post.topic = {post.topic}")
        logger.debug(f"QAAgent DEBUG: post.primary_keyword = {post.primary_keyword}")
        logger.debug(f"QAAgent DEBUG: post.target_audience = {post.target_audience}")
        logger.debug(f"QAAgent DEBUG: prompts dict keys = {list(self.prompts.keys())}")
        
        # Verify qa_review template exists before using it
        if "qa_review" not in self.prompts:
            available_templates = list(self.prompts.keys())
            error_msg = f"qa_review template not found in prompts. Available templates: {available_templates}"
            logger.error(f"QAAgent ERROR: {error_msg}")
            raise KeyError(error_msg)

        try:
            prompt = self.prompts["qa_review"].format(
                primary_keyword=post.primary_keyword,
                target_audience=post.target_audience,
                draft=previous_content,
            )
        except KeyError as e:
            logger.error(f"QAAgent: Format string error - missing key: {e}")
            logger.error(f"QAAgent: Template = {self.prompts['qa_review'][:200]}...")
            raise

        try:
            response_data = await self.llm_client.generate_json(prompt)
        except Exception as e:
            logger.error(f"QAAgent: Failed to get JSON from LLM: {e}")
            # Fallback: return generic rejection with error message
            return False, f"QA system encountered an error: {str(e)[:100]}. Manual review recommended."

        # Validate response_data structure
        if not isinstance(response_data, dict):
            logger.error(f"QAAgent: Expected dict, got {type(response_data).__name__}")
            return False, "QA response was malformed. Manual review recommended."

        # Parse the JSON response with validation
        try:
            approved = response_data.get("approved", False)
            feedback = response_data.get("feedback", "No feedback provided.")
            
            # Validate feedback is a string
            if not isinstance(feedback, str):
                feedback = str(feedback) if feedback else "No feedback provided."
            
            # Ensure feedback is not empty
            feedback = feedback.strip() if feedback else "No feedback provided."
            if not feedback or feedback == "null" or feedback == "None":
                feedback = "QA review completed. Content ready for approval decision."
            
            # Validate approved is boolean
            if not isinstance(approved, bool):
                approved = str(approved).lower() in ["true", "yes", "1"]
        except Exception as e:
            logger.error(f"QAAgent: Error parsing response data: {e}")
            return False, f"QA feedback parsing error. Content requires manual review."

        logger.info(f"QAAgent: Review complete - Approved={approved}, Feedback={feedback[:100]}...")
        
        if approved:
            return True, "Content approved by QA."
        else:
            return False, feedback
